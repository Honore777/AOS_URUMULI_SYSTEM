from decimal import Decimal
from utils import to_decimal, to_number, _convert_decimals
print(to_decimal('123.45'))
print(type(to_decimal('123.45')))
print(to_number(Decimal('67.89')))
print(_convert_decimals({'a': Decimal('1.23'), 'b':[Decimal('2.34'), 5]}))
