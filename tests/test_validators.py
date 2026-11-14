from prodigal_assignment.validators import validate_field, analyze_candidate


def test_invalid_values():
    assert not validate_field('email', 'kabir@gmail')[0]
    assert not validate_field('cgpa', 85)[0]
    assert not validate_field('graduation_year', 2031)[0]
    assert not validate_field('available_from', '2028-01-01')[0]
    assert not validate_field('internship_duration_months', 4)[0]
    assert not validate_field('github_url', 'https://evil.com/john')[0]


def test_normalization():
    assert validate_field('preferred_location', 'Bangalore')[1] == 'Bengaluru'
    assert validate_field('cgpa', '8.70')[1] == 8.7
    assert validate_field('graduation_year', '2027')[1] == 2027
    assert validate_field('internship_duration_months', '6')[1] == 6


def test_analysis_detects_invalid_not_just_null():
    candidate = {
        'id': 'c008', 'full_name': 'Meera Joshi', 'phone': '+919876543210',
        'email': 'meera@example.com', 'college': 'NMIMS', 'degree': 'B.Tech',
        'branch': 'Computer Science', 'graduation_year': 2031,
        'cgpa': None, 'preferred_location': 'Remote',
        'available_from': '2027-03-01', 'internship_duration_months': 2,
    }
    report = analyze_candidate(candidate)
    assert report['missing_fields'] == ['cgpa']
    assert 'graduation_year' in report['invalid_fields']
