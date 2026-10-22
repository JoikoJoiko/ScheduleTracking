from django.conf import settings


def release(request):
    return {'site_version':settings.SITE_VERSION}
