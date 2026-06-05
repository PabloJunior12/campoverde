from apps.tramite.models import Department, Province, District
import re

def parse_origin_options(value):
    if not value:
        return []

    mapping = {
        "Conocimiento y fines": "INFO",
        "Autorizado": "AUTHORIZED",
    }

    result = []

    for opt in value.split(","):
        clean = opt.strip()
        if clean in mapping:
            result.append(mapping[clean])

    return result


def extract_sequence_and_year(code):
    """
    '000001-2025' -> (1, 2025)
    """
    try:
        seq_part, year_part = code.split("-")
        return int(seq_part), int(year_part)
    except Exception:
        return None, None

def resolve_location_from_procedencia(procedencia):

    if not procedencia:
        return None, None, None

    try:
        dep_desc, prov_desc, dist_desc = [
            p.strip() for p in procedencia.split("-")
        ]

        department = Department.objects.filter(
            description__iexact=dep_desc,
            active=True
        ).first()

        province = None
        district = None

        if department:
            province = Province.objects.filter(
                description__iexact=prov_desc,
                department=department,
                active=True
            ).first()

        if province:
            district = District.objects.filter(
                description__iexact=dist_desc,
                province=province,
                active=True
            ).first()

        return department, province, district

    except ValueError:
        # formato incorrecto
        return None, None, None
