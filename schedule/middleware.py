import time
import logging
from django.core.cache import cache
from django.http import JsonResponse

class ApiGuard:
    def __init__(self, get_response):
        self.get_response = get_response
    def __call__(self, request):
        if request.path.startswith('/api/'):
            # Per-process guard; use an upstream shared rate limit in production.
            key = f'api:{request.META.get("REMOTE_ADDR")}:{int(time.time() // 60)}'
            if cache.add(key, 1, 65):
                count = 1
            else:
                count = cache.incr(key)
            if count > 180:
                response = JsonResponse({'error':{'code':'rate_limit','message':'Слишком много запросов. Повторите через минуту.'}}, status=429)
                response['Retry-After'] = '60'
                return response
        return self.get_response(request)
