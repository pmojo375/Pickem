"""CBBD REST adapter. Parameters follow the public CBBD OpenAPI contract."""
from .base import QuotaClient, ProviderError


class BasketballProvider(QuotaClient):
    def __init__(self, **kwargs):
        super().__init__("https://api.collegebasketballdata.com", "cbbd", **kwargs)

    def teams(self, season):
        return self.get("/teams", {"season": season}, ttl=86400)

    def games(self, season, start, end, season_type=None, tournament=None):
        params = {"season": season, "startDateRange": start, "endDateRange": end}
        if season_type:
            params["seasonType"] = season_type
        if tournament:
            params["tournament"] = tournament
        return self._bounded("/games", params)

    def lines(self, season, start, end):
        return self._bounded("/lines", {"season": season, "startDateRange": start, "endDateRange": end})

    def _bounded(self, endpoint, params):
        data = self.get(endpoint, params)
        if len(data) >= 3000:
            raise ProviderError("Response reached the provider's 3,000-game cap. Use a narrower date range.")
        return data
