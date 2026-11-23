"""Reconstruct version 1 segments using only stable identities and fixed columns."""

import json
from hashlib import sha256
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from .backup import BASE_FIELDS, MAX_KEYS, checked_subjects, parse_scope
from .backup_authority import canonical, identity, wire
from .backup_projection import (
    FORMAT,
    KEY_COLUMNS,
    PROVENANCE_FIELDS,
    REVISION_COLUMNS,
    SITE_COLUMNS,
    catalog_row,
    period_from_wire,
    projection,
)
from .models import City, CityIdentifier, Product, SiteEpoch, Source, Station, StationIdentifier
from .policy import Denied
from .revisions import key_scope, tables


def exact(data, columns):
    if not isinstance(data, dict) or set(data) != set(columns):
        raise Denied("backup_integrity_failed")
    return data.copy()


def manifest_matches(product, segment):
    manifest = segment.manifest
    expected = wire(
        {
            "format": FORMAT,
            "migration": "0013_backup_segments",
            "segment": str(segment.pk),
            "product": identity(product),
            "profile": product.contract_version,
            "level": product.level,
            "shape": product.shape,
            "family": product.family,
            "source_timezone": product.source_timezone,
            "scope": segment.scope,
            "fields": segment.fields,
            "snapshot_version": segment.snapshot_version,
            "snapshot_at": segment.snapshot_at,
            "expires_at": segment.expires_at,
        }
    )
    tail = {"attribution", "source_publisher", "keys", "bytes", "sha256", "catalog"}
    exact(manifest, set(expected) | tail)
    if any(manifest[key] != value for key, value in expected.items()):
        raise Denied("backup_integrity_failed")
    if (
        set(segment.fields) not in (BASE_FIELDS, BASE_FIELDS | {"raw_field"})
        or type(manifest["keys"]) is not int
        or not 1 <= manifest["keys"] <= MAX_KEYS
        or type(manifest["bytes"]) is not int
        or manifest["bytes"] <= 0
        or not isinstance(manifest["catalog"], list)
    ):
        raise Denied("backup_integrity_failed")
    return manifest


def scalar_columns(model, data):
    """Accept the exact canonical wire representation of each model field."""
    result = {}
    for name, value in data.items():
        field = model._meta.get_field(name)
        decoded = None if value is None and field.null else field.clean(value, None)
        if type(wire(decoded)) is not type(value) or wire(decoded) != value:
            raise Denied("backup_integrity_failed")
        result[name] = decoded
    return result


def decode_period(value):
    if not isinstance(value, list) or len(value) != 2:
        raise Denied("backup_integrity_failed")
    period = period_from_wire(value)
    if (
        period.lower is None
        or period.upper is None
        or period.lower.tzinfo is None
        or period.upper.tzinfo is None
        or period.lower >= period.upper
    ):
        raise Denied("backup_integrity_failed")
    return period


def subject_bindings():
    return {subject["token"]: subject["identity"] for subject in checked_subjects().values()}


class ImportedSegment:
    def __init__(self, alias, manifest):
        self.alias = alias
        self.manifest = manifest
        self.scope = parse_scope(manifest["scope"])
        self.source = Source.objects.using(alias).create(
            namespace=manifest["product"][0], publisher=manifest["source_publisher"]
        )
        self.product = Product.objects.using(alias).create(
            source=self.source,
            code=manifest["product"][1],
            family=manifest["family"],
            shape=manifest["shape"],
            level=manifest["level"],
            synthetic=manifest["family"] == "F",
            source_timezone=manifest["source_timezone"],
            contract_version=manifest["profile"],
            attribution=manifest["attribution"],
            rights_version=manifest["snapshot_version"],
            collection_pause="isolated_restore",
        )
        self.key_model, self.rev_model, self.entity, _, _ = tables(self.product.level)
        self.subject_bindings = subject_bindings()
        self.subjects = {}
        self.directories = {}
        self.revision_tokens = {}
        self.order = []
        self.dependencies = set()

    def create(self, model, **data):
        return model.objects.using(self.alias).create(**data)

    def identifier(self, data, model, entity, obj):
        data = exact(data, ("period", "code", "evidence"))
        return self.create(
            model,
            **scalar_columns(model, {k: data[k] for k in ("code", "evidence")}),
            active_period=decode_period(data["period"]),
            source=self.source,
            **{entity: obj},
        )

    def directory(self, data):
        data = exact(data, ("city", "station"))
        city = exact(data["city"], ("name", "geography", "identifier"))
        city_key = canonical(city)
        if city_key not in self.directories:
            obj = self.create(
                City,
                source=self.source,
                **scalar_columns(City, {k: city[k] for k in ("name", "geography")}),
            )
            self.identifier(city["identifier"], CityIdentifier, "city", obj)
            self.directories[city_key] = obj
        city = self.directories[city_key]
        if self.entity == "city":
            if data["station"] is not None:
                raise Denied("backup_integrity_failed")
            return city
        station = exact(data["station"], ("name", "identifier", "epoch"))
        identifier_key = canonical({"name": station["name"], "identifier": station["identifier"]})
        if identifier_key not in self.directories:
            obj = self.create(
                Station, source=self.source, **scalar_columns(Station, {"name": station["name"]})
            )
            self.identifier(station["identifier"], StationIdentifier, "station", obj)
            self.directories[identifier_key] = obj
        epoch_key = canonical(data)
        if epoch_key not in self.directories:
            epoch = exact(station["epoch"], (*SITE_COLUMNS, "period"))
            period = decode_period(epoch.pop("period"))
            self.directories[epoch_key] = self.create(
                SiteEpoch,
                station=self.directories[identifier_key],
                city=city,
                active_period=period,
                **scalar_columns(SiteEpoch, epoch),
            )
        return self.directories[epoch_key]

    def subject(self, binding):
        binding = exact(binding, ("token", "identity"))
        token, identity = binding["token"], binding["identity"]
        if (
            not isinstance(token, str)
            or self.subject_bindings.get(token) != identity
            or not identity
        ):
            raise Denied("backup_history_subject_unmapped")
        if token not in self.subjects:
            user = get_user_model()(username=f"history-{uuid4().hex}", is_active=False)
            user.set_unusable_password()
            user.save(using=self.alias)
            self.subjects[token] = user
        return self.subjects[token]

    def insert(self, row):
        row = exact(row, ("directory", "key", "revisions"))
        entity = self.directory(row["directory"])
        key = self.create(
            self.key_model,
            product=self.product,
            **{self.entity: entity},
            **scalar_columns(self.key_model, exact(row["key"], KEY_COLUMNS)),
        )
        scope = key_scope(key)
        if not (
            scope.geography == self.scope.geography
            and scope.object_code == self.scope.object_code
            and scope.indicator == self.scope.indicator
            and self.scope.start <= scope.start < self.scope.end
        ):
            raise Denied("backup_integrity_failed")
        revisions = row["revisions"]
        if not isinstance(revisions, list) or not 1 <= len(revisions) <= 10000:
            raise Denied("backup_integrity_failed")
        raw = ("raw_value", "raw_unit") if "raw_field" in self.manifest["fields"] else ()
        for item in revisions:
            data = exact(item, (*REVISION_COLUMNS, *raw, "provenance", "subject"))
            token, provenance = data.pop("subject"), data.pop("provenance")
            if not isinstance(provenance, dict) or set(provenance) - set(PROVENANCE_FIELDS):
                raise Denied("backup_integrity_failed")
            data = scalar_columns(self.rev_model, data)
            created = data.pop("created_at")
            rev = self.create(
                self.rev_model, key=key, actor=self.subject(token), provenance=provenance, **data
            )
            self.rev_model.objects.using(self.alias).filter(pk=rev.pk).update(created_at=created)
            self.revision_tokens[rev.actor_id] = token
        self.order.append(key.pk)

    def confirm_subjects(self):
        current = subject_bindings()
        if any(
            current.get(row["token"]) != row["identity"] for row in self.revision_tokens.values()
        ):
            raise Denied("backup_history_subject_unmapped")

    def read(self):
        """Reproject actual target rows and independently compare their canonical bytes."""
        checksum = sha256()
        revisions = 0
        scopes = set()
        for pk in self.order:
            key = self.key_model.objects.using(self.alias).get(pk=pk)
            rows = list(
                self.rev_model.objects.using(self.alias).filter(key=key).order_by("revision_no")
            )
            directory, dependencies = catalog_row(self.product, key, rows, alias=self.alias)
            scopes.update(dependency for dependency in dependencies)
            items = []
            for row in rows:
                columns = (*REVISION_COLUMNS, "provenance")
                if "raw_field" in self.manifest["fields"]:
                    columns += ("raw_value", "raw_unit")
                items.append(
                    projection(row, columns) | {"subject": self.revision_tokens[row.actor_id]}
                )
            revisions += len(items)
            checksum.update(
                canonical(
                    {
                        "directory": directory,
                        "key": projection(key, KEY_COLUMNS),
                        "revisions": items,
                    }
                )
                + b"\n"
            )
        actual = checksum.hexdigest()
        expected_scopes = sorted(canonical(item) for item in self.manifest["catalog"])
        if (
            len(self.order) != self.manifest["keys"]
            or actual != self.manifest["sha256"]
            or sorted(canonical(scope.serialize()) for scope in scopes) != expected_scopes
        ):
            raise Denied("backup_integrity_failed")
        return {"keys": len(self.order), "revisions": revisions, "verified": True}

    def load(self, stream):
        try:
            for line in stream:
                if len(self.order) >= self.manifest["keys"]:
                    raise Denied("backup_integrity_failed")
                item = json.loads(line)
                if canonical(item) + b"\n" != line:
                    raise Denied("backup_integrity_failed")
                self.insert(item)
            return self.read()
        except Denied:
            raise
        except (ValueError, KeyError, TypeError, ValidationError, OverflowError):
            raise Denied("backup_integrity_failed") from None
