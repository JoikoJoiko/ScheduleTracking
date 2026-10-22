"""Private subprocess entry point; the parent supplies a disposable SQLite snapshot."""
import json
import sys
from pathlib import Path
from django.conf import settings


def main():
    snapshot=Path(sys.argv[1]).resolve(strict=True)
    batch_id=int(sys.argv[2])
    # Configure the copy before Django opens any connection, even with DATABASE_URL set.
    settings.DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':snapshot,'OPTIONS':{'timeout':20}}}
    import django
    django.setup()
    from django.db import connections
    from django.core.serializers.json import DjangoJSONEncoder
    from .models import ImportBatch
    from .importing import _calculate_import_report
    try:
        report=_calculate_import_report(ImportBatch.objects.select_related('period','profile').get(pk=batch_id))
        print(json.dumps(report,ensure_ascii=False,cls=DjangoJSONEncoder))
    finally:
        connections.close_all()


if __name__=='__main__':
    main()
