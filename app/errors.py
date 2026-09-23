class UpstreamError(Exception):
    """Raised when an external API (Open-Meteo, OpenRouter) fails, times out, or is unreachable."""


class CityNotFoundError(Exception):
    """Raised when geocoding finds no match for the requested city name."""


class ItineraryError(Exception):
    """Raised when the OpenRouter itinerary suggestion cannot be produced."""
