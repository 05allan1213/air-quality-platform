"""Explicit Admin whitelist. Account writes use the same service as the JSON API."""

from django.contrib import admin
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.views.decorators.http import require_POST
from observations.policy import Denied

from .governance import MANAGE, account_action
from .session_generation import bind_generation, lock_login_name


class StaffLogin(AdminAuthenticationForm):
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.has_perm(MANAGE) or self.request.locked_login_id != user.pk:
            raise ValidationError(
                self.error_messages["invalid_login"],
                code="invalid_login",
                params={"username": "username"},
            )


class RestrictedSite(admin.AdminSite):
    login_form = StaffLogin
    site_header = "账号治理"
    site_url = None

    def has_permission(self, request):
        return super().has_permission(request) and request.user.has_perm(MANAGE)

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
        return self.admin_site.has_permission(request)

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
        from django.template.response import TemplateResponse

        if not self.has_module_permission(request):
            raise PermissionDenied
        try:
            if request.method == "GET":
                from uuid import uuid4

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
