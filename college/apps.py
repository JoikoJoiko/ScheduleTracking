from django.contrib.admin.apps import AdminConfig


class CollegeAdminConfig(AdminConfig):
    default_site = 'college.admin.CollegeAdminSite'
