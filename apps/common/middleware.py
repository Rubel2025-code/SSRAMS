"""
Lightweight request middleware that exposes role-derived helpers on
``request`` for every view/template without each app re-deriving them.
"""


class RoleContextMiddleware:
    """
    Attaches convenience booleans to ``request`` for the authenticated
    user's role, so views and templates can branch without importing
    RoleChoices directly. Business authorization decisions still belong
    in apps.common.rbac — this middleware only sets read-only context.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            request.is_student = user.role == "student"
            request.is_provider = user.role == "provider"
            request.is_admin_role = user.role == "admin"
        else:
            request.is_student = False
            request.is_provider = False
            request.is_admin_role = False

        return self.get_response(request)
