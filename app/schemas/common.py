from typing import Literal


Role = Literal["admin", "doctor", "patient"]


ADMIN_ROLES: frozenset[str] = frozenset({"admin"})


Weekday = Literal[
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]
