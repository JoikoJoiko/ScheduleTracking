from django.contrib.admin import AdminSite
from django.urls import path


class CollegeAdminSite(AdminSite):
    def index(self, request, extra_context=None):
        from schedule.dashboard import dashboard_context
        context = dashboard_context(request.user)
        context.update(extra_context or {})
        return super().index(request, extra_context=context)

    def get_urls(self):
        from schedule import dashboard
        return [
            path('uploads/schedule/', self.admin_view(dashboard.upload_schedule), name='upload_schedule'),
            path('uploads/changes/', self.admin_view(dashboard.upload_changes), name='upload_changes'),
            path('uploads/status/', self.admin_view(dashboard.upload_status), name='upload_status'),
        ] + super().get_urls()
