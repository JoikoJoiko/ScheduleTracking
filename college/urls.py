from django.contrib import admin
from django.urls import path, re_path
from schedule import views as v

urlpatterns = [
    path('admin/conflicts/',v.conflicts_page),
    path('admin/',admin.site.urls),
    path('api/meta',v.meta),
    path('api/rooms/availability',v.room_availability),
    path('api/changes',v.changes),
    path('api/academic-years',v.years),
    path('api/admin/imports',v.admin_imports),
    path('api/admin/imports/<int:pk>',v.admin_imports),
    path('api/admin/imports/<int:pk>/<str:action>',v.admin_imports),
    path('api/admin/changes',v.admin_changes),
    path('api/admin/changes/<int:pk>',v.admin_changes),
    path('api/admin/changes/<int:pk>/<str:action>',v.admin_changes),
    path('api/admin/conflicts',v.admin_conflicts),
    path('api/admin/academic-years',v.admin_years),
    path('api/admin/rollover',v.admin_rollover),
    path('api/<str:kind>/<int:pk>/schedule',v.schedule),
    path('api/<str:kind>',v.catalog),
    re_path(r'^(?:groups|teachers|rooms)(?:/\d+)?/?$',v.page),
    path('changes',v.page),
    path('bells',v.page),
    path('',v.page),
]
