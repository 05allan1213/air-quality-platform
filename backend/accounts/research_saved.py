"""Owned analysis intents, with an optional purpose-limited statistical baseline."""

from datetime import datetime

from django.views.decorators.cache import never_cache
from observations.analysis_query import build_relation
from observations.policy import Denied
from observations.research_api import PARENT, bounded
from observations.research_spec import METHOD_VERSION, object_with_keys, parse_spec, positive_int
from observations.s04_http import params
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import SavedQuery, SavedQueryTarget
from .profile import valid_text
from .research_session import (
    SessionCheck,
    authorize_inputs,
    lock_dependencies,
    owned,
    paged,
    reject,
    request_transaction,
    scope,
)


def targets(item):
    return sorted(item.targets.all(), key=lambda target: target.position)


def unavailable(item):
    return item.product_id is None or any(target.entity_id is None for target in targets(item))


def projection(item):
    # Ordinal target FKs are authoritative; a deleted target cannot be reconstructed from JSON.
    selection = dict(item.intent["selection"], product=item.product_id)
    selection_key = "cities" if "cities" in selection else "site_epochs"
    selection[selection_key] = [target.entity_id for target in targets(item)]
    return {
        "id": str(item.pk),
        "title": item.title,
        "intent_revision": item.intent_revision,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        **(item.intent | {"selection": selection}),
    }


def payload(data, *, update=False):
    required = {"intent_revision"} if update else {"title", "selection", "method"}
    object_with_keys(
        data, PARENT | {"title"} | ({"intent_revision"} if update else set()), required
    )
    if "title" in data and (not valid_text(data["title"], 100) or not data["title"].strip()):
        raise Denied("invalid_request")
    parent = {key: data[key] for key in PARENT & data.keys()}
    if not (parent or "title" in data):
        raise Denied("invalid_request")
    return parent


def research_spec(parent):
    spec = parse_spec(parent)
    if spec.method == "detail":
        raise Denied("invalid_request")
    return spec


def relation_for(auth, spec):
    (access,) = authorize_inputs(auth.user_id, [spec], now=auth.now)
    return build_relation(auth.user_id, spec, ctx=access.ctx, access=access)


def retained_baseline_basis(relation):
    return [entry for entry in relation.basis if "statistics_baseline" in entry["fields"]]


def save(item, spec, title, relation):
    # Put the only current target references in nullable FKs.
    intent = spec.canonical
    intent["selection"].pop(spec.entity_key)
    intent["selection"][spec.entity_key] = []
    intent["selection"].pop("product")
    item.intent = intent
    item.title = title.strip()
    item.product_id = spec.product_id
    if relation.allow_baseline():
        item.baseline = relation.content_version
        item.baseline_basis = retained_baseline_basis(relation)
        item.baseline_until = min(
            (datetime.fromisoformat(row["until"]) for row in item.baseline_basis if row["until"]),
            default=None,
        )
    else:
        item.baseline, item.baseline_basis, item.baseline_until = "", [], None
    item.save()
    item.targets.all().delete()
    city = spec.entity_key == "cities"
    SavedQueryTarget.objects.bulk_create(
        SavedQueryTarget(
            query=item,
            position=index,
            kind="city" if city else "station",
            **{"city_id" if city else "site_epoch_id": entity},
        )
        for index, entity in enumerate(spec.ids)
    )
    return projection(item)


def current_spec(item):
    # Preserve an obsolete method for use_changed reporting. Only actual nulling makes a target unavailable.
    current = projection(item)
    return research_spec({key: current[key] for key in PARENT})


@never_cache
@api_view(["GET", "POST"])
def saved_queries(request):
    try:
        with request_transaction():
            if request.method == "GET":
                return paged(SavedQuery, request, "saved-queries", projection, prefetch="targets")
            query = params(request.query_params, {"scope"})
            data = request.data
            spec = research_spec(payload(data))
            if query and scope(query) != spec.synthetic:
                raise Denied("invalid_request")
            authorize_inputs(request.user.pk, [spec])
            lock_dependencies([spec])
            auth = SessionCheck(request)
            relation = relation_for(auth, spec)
            item = SavedQuery(owner_id=auth.user_id, synthetic_scope=spec.synthetic)
            result = save(item, spec, data["title"], relation)
            auth.finish([relation])
        return Response(result, status=201)
    except Denied as exc:
        return reject(exc)


@never_cache
@api_view(["GET", "PATCH", "DELETE"])
def saved_query(request, item_id):
    try:
        synthetic = scope(params(request.query_params, {"scope"}))
        user_id = request.user.pk
        with request_transaction():
            before = owned(SavedQuery, user_id, synthetic, item_id)
            if request.method == "GET":
                SessionCheck(request)
                return Response(projection(before))
            if request.method == "DELETE":
                item = owned(SavedQuery, user_id, synthetic, item_id, lock=True)
                SessionCheck(request)
                item.delete()
                return Response(status=204)
            data = request.data
            new_parts = payload(data, update=True)
            revision = positive_int(data["intent_revision"])
            old_parent = {key: value for key, value in projection(before).items() if key in PARENT}
            spec = research_spec(old_parent | new_parts)
            if synthetic != spec.synthetic:
                raise Denied("invalid_request")
            old_spec = None if unavailable(before) else current_spec(before)
            authorize_inputs(user_id, [spec])
            lock_dependencies([spec, *([old_spec] if old_spec else [])])
            item = owned(SavedQuery, user_id, synthetic, item_id, lock=True)
            auth = SessionCheck(request)
            relation = relation_for(auth, spec)
            if item.intent_revision != revision or item.intent_revision != before.intent_revision:
                raise Denied("input_changed")
            item.intent_revision += 1
            result = save(item, spec, data.get("title", item.title), relation)
            auth.finish([relation])
        return Response(result)
    except Denied as exc:
        return reject(exc)


@never_cache
@api_view(["POST"])
def run_saved_query(request, item_id):
    from observations.research_statistics import summarize

    try:
        synthetic = scope(params(request.query_params, {"scope"}))
        if request.data:
            raise Denied("invalid_request")
        user_id = request.user.pk
        with request_transaction():
            before = owned(SavedQuery, user_id, synthetic, item_id)
            spec = None if unavailable(before) else current_spec(before)
            if spec:
                authorize_inputs(user_id, [spec])
                lock_dependencies([spec])
            item = owned(SavedQuery, user_id, synthetic, item_id, lock=True)
            auth = SessionCheck(request)
            if unavailable(item):
                return Response({"state": "target_unavailable"})
            if spec is None or item.intent_revision != before.intent_revision:
                raise Denied("input_changed")
            try:
                relation = relation_for(auth, spec)
            except Denied as exc:
                if exc.code == "method_changed":
                    return Response({"state": "method_changed", "method_version": METHOD_VERSION})
                raise
            state = "baseline_unavailable"
            if item.baseline and (item.baseline_until is None or item.baseline_until > auth.now):
                if relation.allow_baseline():
                    state = (
                        "current" if item.baseline == relation.content_version else "data_changed"
                    )
            output = bounded(
                request,
                {
                    "state": state,
                    "intent_revision": item.intent_revision,
                    "result": (
                        summarize(relation, series=relation.series)
                        | spec.canonical
                        | {
                            "input_version": relation.input_version,
                            "source_timezone": relation.product.source_timezone,
                            "attribution": relation.attributions,
                            "requested_window": {"start": spec.start, "end": spec.end},
                            **relation.metadata,
                        }
                    ),
                },
            )
            auth.finish([relation])
        return output
    except Denied as exc:
        return reject(exc)
