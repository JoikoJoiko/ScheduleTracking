from django.contrib.admin import AdminSite
from django.urls import path


class CollegeAdminSite(AdminSite):
    def advanced(self,request):
        from django.core.exceptions import PermissionDenied
        from django.shortcuts import render
        if not request.user.is_superuser:
            raise PermissionDenied
        return render(request,'admin/advanced.html',dict(self.each_context(request),title='Технические настройки',app_list=self.get_app_list(request)))

    def index(self, request, extra_context=None):
        from schedule.dashboard import dashboard_context
        context = dashboard_context(request.user)
        context.update(extra_context or {})
        return super().index(request, extra_context=context)

    def get_urls(self):
        from schedule import dashboard,operations
        return [
            path('bells/',self.admin_view(lambda request:operations.bells_editor(request,self)),name='bells_editor'),
            path('practices/',self.admin_view(lambda request:operations.practices_editor(request,self)),name='practices_editor'),
            path('advanced/',self.admin_view(self.advanced),name='advanced_settings'),
            path('uploads/schedule/', self.admin_view(dashboard.upload_schedule), name='upload_schedule'),
            path('uploads/changes/', self.admin_view(dashboard.upload_changes), name='upload_changes'),
            path('uploads/status/', self.admin_view(dashboard.upload_status), name='upload_status'),
        ] + super().get_urls()
