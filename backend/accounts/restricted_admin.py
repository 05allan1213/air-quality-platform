"""Explicit Admin whitelist. Account writes use the same service as the JSON API."""

from uuid import uuid4

from django.contrib import admin
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST
from observations.policy import Denied
from observations.s04_http import decode_cursor, encode_cursor, params

from .assigned_permissions import MAINTAIN_CITY_ALIAS, has_assigned_permission
from .governance import MANAGE, account_action
from .session_generation import bind_generation, lock_login_name

ALIAS_DIRECTORY_SIZE = 25


class StaffLogin(AdminAuthenticationForm):
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not (user.has_perm(MANAGE) or has_assigned_permission(user, MAINTAIN_CITY_ALIAS)) or (
            self.request.locked_login_id != user.pk
        ):
            raise ValidationError(
                self.error_messages["invalid_login"],
                code="invalid_login",
                params={"username": "username"},
            )


class RestrictedSite(admin.AdminSite):
    index_template = "admin/restricted_index.html"
    login_form = StaffLogin
    site_header = "账号治理"
    site_url = None

    def has_permission(self, request):
        return super().has_permission(request) and (
            request.user.has_perm(MANAGE)
            or has_assigned_permission(request.user, MAINTAIN_CITY_ALIAS)
        )

    def get_urls(self):
        return [
            path(
                "observations/city-search-alias/",
                self.admin_view(city_search_alias_directory),
                name="city_search_alias_directory",
            ),
            path(
                "observations/city-search-alias/<int:product_id>/<int:city_id>/",
                self.admin_view(city_search_alias),
                name="city_search_alias",
            ),
            *super().get_urls(),
        ]

    def index(self, request, extra_context=None):
        return super().index(
            request,
            {
                **(extra_context or {}),
                "can_maintain_search_alias": has_assigned_permission(
                    request.user, MAINTAIN_CITY_ALIAS
                ),
            },
        )

    @transaction.atomic
    def login(self, request, extra_context=None):
        if request.method == "POST":
            user = lock_login_name(request.POST.get("username"))
            request.locked_login_id = user.pk if user else None
        response = super().login(request, extra_context)
        if (
            request.method == "POST"
            and response.status_code == 302
            and request.user.is_authenticated
        ):
            bind_generation(request, request.user.pk)
        return response


site = RestrictedSite(name="admin")
site.disable_action("delete_selected")


@admin.register(get_user_model(), site=site)
class AccountDirectory(admin.ModelAdmin):
    list_display = ("id", "is_active", "controls")
    list_display_links = None
    actions = None

    # Disallow generic object/history URLs, password resets and writable fields.
    def get_urls(self):
        meta = self.model._meta
        return [
            path(
                "",
                self.admin_site.admin_view(self.changelist_view),
                name=f"{meta.app_label}_{meta.model_name}_changelist",
            ),
            path(
                "<int:pk>/actions/",
                self.admin_site.admin_view(self.control),
                name="account_controls",
            ),
        ]

    def has_module_permission(self, request):
        return self.admin_site.has_permission(request) and request.user.has_perm(MANAGE)

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return False

    has_delete_permission = has_add_permission = has_change_permission

    def controls(self, obj):
        return format_html(
            '<a href="{}">{}</a>', reverse("admin:account_controls", args=[obj.pk]), "管理"
        )

    def control(self, request, pk):
        if not self.has_module_permission(request):
            raise PermissionDenied
        try:
            if request.method == "GET":
                return TemplateResponse(
                    request,
                    "admin/account_controls.html",
                    {
                        **self.admin_site.each_context(request),
                        **account_action(request, pk),
                        "request_id": uuid4(),
                    },
                )
            return require_POST(self.apply_control)(request, pk)
        except Denied as exc:
            return JsonResponse(
                {"code": exc.code},
                status={
                    "precondition_conflict": 409,
                    "request_conflict": 409,
                    "not_found": 404,
                    "invalid_request": 400,
                }.get(exc.code, 403),
            )

    def apply_control(self, request, pk):
        data = request.POST.dict()
        data.pop("csrfmiddlewaretoken", None)
        try:
            data["expected_version"] = int(data["expected_version"])
        except (KeyError, ValueError):
            raise Denied("invalid_request") from None
        return JsonResponse(account_action(request, pk, data))


@never_cache
@require_GET
def city_search_alias_directory(request):
    from observations.city_search_alias import list_targets

    if not has_assigned_permission(request.user, MAINTAIN_CITY_ALIAS):
        return _alias_error(Denied("permission_denied"))
    try:
        values = params(request.GET, {"cursor"})
        conditions = [ALIAS_DIRECTORY_SIZE]
        position = decode_cursor(
            values.get("cursor"), "admin.city_search_alias", request.user.pk, conditions
        ) or {"product": 0, "city": 0}
        if (
            set(position) != {"product", "city"}
            or type(position["product"]) is not int
            or type(position["city"]) is not int
            or not 0 <= position["product"] < 2**63
            or not 0 <= position["city"] < 2**63
            or (position["product"] == 0 and position["city"] != 0)
        ):
            raise Denied("invalid_cursor")
        rows = list_targets(request, position["product"], position["city"], ALIAS_DIRECTORY_SIZE)
        visible = rows[:ALIAS_DIRECTORY_SIZE]
        next_cursor = None
        if len(rows) > ALIAS_DIRECTORY_SIZE:
            last = visible[-1]
            next_cursor = encode_cursor(
                "admin.city_search_alias",
                request.user.pk,
                conditions,
                {"product": last["product_id"], "city": last["city_id"]},
            )
        return TemplateResponse(
            request,
            "admin/city_search_alias_directory.html",
            {**site.each_context(request), "targets": visible, "next_cursor": next_cursor},
        )
    except Denied as exc:
        return _alias_error(exc)


@never_cache
def city_search_alias(request, product_id, city_id):
    from observations.city_search_alias import read_alias, write_alias

    if not has_assigned_permission(request.user, MAINTAIN_CITY_ALIAS):
        return _alias_error(Denied("permission_denied"))
    if request.GET:
        return _alias_error(Denied("invalid_request"))
    if request.method == "GET":
        try:
            data = read_alias(request, product_id, city_id)
            return TemplateResponse(
                request,
                "admin/city_search_alias.html",
                {
                    **site.each_context(request),
                    **data,
                    "set_request_id": uuid4(),
                    "clear_request_id": uuid4(),
                },
            )
        except Denied as exc:
            return _alias_error(exc)
    try:
        if (
            request.method != "POST"
            or request.content_type != "application/x-www-form-urlencoded"
            or len(request.body) > 16 * 1024
            or any(len(values) != 1 for _, values in request.POST.lists())
        ):
            raise Denied("invalid_request")
        data = request.POST.dict()
        data.pop("csrfmiddlewaretoken", None)
        version = data.get("expected_version", "")
        if not (1 <= len(version) <= 19 and version.isascii() and version.isdigit()):
            raise Denied("invalid_request")
        data["expected_version"] = int(version)
        result = write_alias(request, product_id, city_id, data)
        return JsonResponse(result)
    except Denied as exc:
        return _alias_error(exc)


def _alias_error(exc):
    return JsonResponse(
        {"code": exc.code},
        status={
            "permission_denied": 403,
            "use_denied": 403,
            "not_authenticated": 403,
            "not_found": 404,
            "invalid_request": 400,
            "precondition_conflict": 409,
            "request_conflict": 409,
            "page_changed": 409,
            "database_unavailable": 503,
        }.get(exc.code, 400),
    )
