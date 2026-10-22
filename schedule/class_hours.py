"""One canonical title for current and historical names in source workbooks."""
import re

TITLE = 'Разговоры о важном'
ALIAS = 'Кураторский час'


def is_class_hour(value):
    text = re.sub(r'[^а-яё]', '', str(value).casefold())
    return text.startswith(('разговорыоваж', 'рахговорыоваж', 'кураторскийчас', 'классныйчас'))
