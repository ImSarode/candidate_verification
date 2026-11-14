import re
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse
from pydantic import EmailStr, TypeAdapter, ValidationError

MANDATORY_FIELDS = [
    'full_name', 'phone', 'email', 'college', 'degree', 'branch',
    'graduation_year', 'cgpa', 'preferred_location', 'available_from',
    'internship_duration_months',
]
OPTIONAL_FIELDS = {'linkedin_url', 'github_url', 'skills'}
DEGREES = {'B.Tech', 'B.E.', 'B.Sc', 'BCA', 'MBA Tech', 'MCA', 'M.Tech', 'Other'}
LOCATIONS = {'Mumbai', 'Bengaluru', 'Remote', 'No preference'}
EMAIL = TypeAdapter(EmailStr)


def is_missing(value):
    return value is None or (isinstance(value, str) and not value.strip())


def _url(value, domain, profile=False):
    if not isinstance(value, str):
        raise ValueError('Expected a URL string')
    parsed = urlparse(value.strip())
    host = (parsed.hostname or '').lower()
    if parsed.scheme not in {'http', 'https'} or not (host == domain or host == f'www.{domain}'):
        raise ValueError(f'Expected a {domain} URL')
    if not parsed.path or parsed.path == '/':
        raise ValueError('Expected a profile URL, not a homepage')
    if parsed.username or parsed.password:
        raise ValueError('URL must not contain credentials')
    if profile and not re.fullmatch(r'/[A-Za-z0-9-]+/?', parsed.path):
        raise ValueError('Expected a GitHub profile URL (not a repository)')
    return value.strip()


def validate_field(field, value):
    """Return (valid, normalized_value, error). No writes happen here."""
    if is_missing(value):
        return False, None, 'Value is missing'
    try:
        if field == 'full_name':
            if not isinstance(value, str) or not 2 <= len(value.strip()) <= 100:
                raise ValueError('Name must be 2–100 characters')
            return True, value.strip(), None
        if field == 'phone':
            if not isinstance(value, str) or not re.fullmatch(r'\+[1-9]\d{1,14}', value):
                raise ValueError('Invalid E.164 phone')
            return True, value, None
        if field == 'email':
            if not isinstance(value, str):
                raise ValueError('Expected email string')
            return True, str(EMAIL.validate_python(value.strip())), None
        if field in {'college', 'branch'}:
            if not isinstance(value, str) or not 2 <= len(value.strip()) <= 150:
                raise ValueError(f'{field} must be 2–150 characters')
            return True, value.strip(), None
        if field == 'degree':
            if not isinstance(value, str) or value not in DEGREES:
                raise ValueError(f'Degree must be one of {sorted(DEGREES)}')
            return True, value, None
        if field == 'preferred_location':
            if not isinstance(value, str):
                raise ValueError('Expected location string')
            aliases = {'bangalore': 'Bengaluru', 'bengaluru': 'Bengaluru',
                       'mumbai': 'Mumbai', 'remote': 'Remote',
                       'no preference': 'No preference'}
            normalized = aliases.get(value.strip().lower())
            if normalized not in LOCATIONS:
                raise ValueError(f'Location must be one of {sorted(LOCATIONS)}')
            return True, normalized, None
        if field in {'graduation_year', 'internship_duration_months'}:
            if isinstance(value, str) and value.strip().isdigit():
                value = int(value.strip())
            if type(value) is not int:
                raise ValueError('Expected integer')
            if field == 'graduation_year' and value not in range(2026, 2029):
                raise ValueError('Graduation year must be 2026–2028')
            if field == 'internship_duration_months' and value not in {2, 3, 6}:
                raise ValueError('Internship duration must be 2, 3, or 6 months')
            return True, value, None
        if field == 'cgpa':
            if isinstance(value, bool):
                raise ValueError('Expected numeric CGPA')
            try:
                number = Decimal(str(value))
            except (InvalidOperation, ValueError):
                raise ValueError('Expected numeric CGPA')
            if not number.is_finite() or not Decimal('0') <= number <= Decimal('10'):
                raise ValueError('CGPA must be on a 0–10 scale')
            if number.as_tuple().exponent < -2:
                raise ValueError('CGPA can have at most two decimal places')
            return True, float(number.quantize(Decimal('0.01'))), None
        if field == 'available_from':
            if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                raise ValueError('Expected date YYYY-MM-DD')
            parsed = date.fromisoformat(value)
            if not date(2026, 1, 1) <= parsed <= date(2027, 12, 31):
                raise ValueError('Date must be between 2026-01-01 and 2027-12-31')
            return True, value, None
        if field == 'linkedin_url':
            return True, _url(value, 'linkedin.com'), None
        if field == 'github_url':
            return True, _url(value, 'github.com', profile=True), None
        if field == 'skills':
            if not isinstance(value, list) or len(value) > 30 or not all(
                isinstance(x, str) and x.strip() for x in value
            ):
                raise ValueError('Skills must be a list of up to 30 nonempty strings')
            return True, [x.strip() for x in value], None
        raise ValueError(f'Unknown field: {field}')
    except (ValueError, TypeError, ValidationError) as exc:
        return False, None, str(exc)


def analyze_candidate(candidate):
    missing, invalid, valid = [], {}, {}
    for field in MANDATORY_FIELDS:
        value = candidate.get(field)
        if is_missing(value):
            missing.append(field)
        else:
            ok, normalized, error = validate_field(field, value)
            if ok:
                valid[field] = normalized
            else:
                invalid[field] = error
    return {
        'candidate_id': candidate.get('id'),
        'candidate_name': candidate.get('full_name'),
        'missing_fields': missing,
        'invalid_fields': invalid,
        'valid_fields': valid,
        'is_complete': not missing and not invalid,
    }
