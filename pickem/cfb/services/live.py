from ..models import Game


def fetch_and_store_live_scores() -> int:
    # TODO: Use ESPN unofficial API to refresh game scores
    updated = 0
    for game in Game.objects.filter(is_selected_for_pickem=True, is_final=False):
        # Placeholder: no-op
        updated += 1
    return updated


