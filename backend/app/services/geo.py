"""
Small geo helper: great-circle (haversine) distance between two
lat/lon points, in kilometers. Used by the "smart assignment"
suggestion logic (routes/deliveries.py) to rank agents by how close
their last-known GPS position is to a delivery's address - the same
math routeOptimizer.js already does client-side for route ordering,
just needed server-side here for ranking agents before assignment.
"""

import math


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0  # Earth's mean radius, km
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c
