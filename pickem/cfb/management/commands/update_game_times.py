"""
Management command to pull games and update kickoff times if they've changed.
This is useful when game times are announced or rescheduled during the season.
"""
from datetime import datetime
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from cfb.models import Season, Game, Week, Team
from cfb.services.cfbd_api import get_cfbd_client
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Pull games from CFBD and update kickoff times if they have changed'

    def add_arguments(self, parser):
        parser.add_argument(
            'year',
            type=int,
            help='Season year to update (e.g., 2025)'
        )
        parser.add_argument(
            '--week',
            type=int,
            help='Specific week to update (if omitted, updates all weeks)',
            default=None
        )
        parser.add_argument(
            '--season-type',
            type=str,
            choices=['regular', 'postseason'],
            default='regular',
            help='Season type (regular or postseason)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without actually updating'
        )

    def handle(self, *args, **options):
        year = options['year']
        week = options['week']
        season_type = options['season_type']
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN MODE - No changes will be saved"))

        # Verify season exists
        try:
            season = Season.objects.get(year=year)
        except Season.DoesNotExist:
            raise CommandError(f'Season {year} does not exist. Run initialize_season first.')

        self.stdout.write(f"Fetching games for {year} {season_type} season" + 
                         (f" week {week}" if week else " (all weeks)"))

        # Get CFBD client and fetch games
        cfbd_client = get_cfbd_client()
        
        if week is not None:
            games_data = cfbd_client.fetch_games(
                year=year,
                season_type=season_type,
                week=week,
                division='fbs'
            )
        else:
            games_data = cfbd_client.fetch_all_season_games(year, season_type)

        if not games_data:
            raise CommandError(f"No games data returned from CFBD API")

        self.stdout.write(f"Fetched {len(games_data)} games from CFBD API")

        # Track statistics
        stats = {
            'total': 0,
            'updated_kickoff': 0,
            'updated_other': 0,
            'not_found': 0,
            'no_changes': 0,
            'created': 0,
        }

        # Create team lookup for faster matching
        teams_by_name = {team.name: team for team in season.teams.all()}

        # Process each game
        for game_data in games_data:
            stats['total'] += 1
            
            # Extract game info
            game_id = game_data.get('id')
            week_number = game_data.get('week')
            home_team_name = game_data.get('homeTeam')
            away_team_name = game_data.get('awayTeam')
            start_date_str = game_data.get('startDate')
            
            if not start_date_str:
                self.stdout.write(
                    self.style.WARNING(f"  ⚠ Game {game_id} has no start date, skipping")
                )
                continue

            # Parse kickoff time
            try:
                new_kickoff = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
                if timezone.is_naive(new_kickoff):
                    new_kickoff = timezone.make_aware(new_kickoff)
            except (ValueError, AttributeError) as e:
                self.stdout.write(
                    self.style.WARNING(f"  ⚠ Invalid start date for game {game_id}: {e}")
                )
                continue

            # Get teams
            home_team = teams_by_name.get(home_team_name)
            away_team = teams_by_name.get(away_team_name)
            
            if not home_team or not away_team:
                self.stdout.write(
                    self.style.WARNING(
                        f"  ⚠ Teams not found for {away_team_name} @ {home_team_name}"
                    )
                )
                stats['not_found'] += 1
                continue

            # Get week object
            try:
                week_obj = Week.objects.get(
                    season=season,
                    number=week_number,
                    season_type=season_type
                )
            except Week.DoesNotExist:
                self.stdout.write(
                    self.style.WARNING(
                        f"  ⚠ Week {week_number} not found for {year} {season_type}"
                    )
                )
                stats['not_found'] += 1
                continue

            # Find existing game
            game = Game.objects.filter(
                season=season,
                home_team=home_team,
                away_team=away_team,
                week=week_obj
            ).first()

            if not game:
                # Game doesn't exist, create it
                if not dry_run:
                    game = Game.objects.create(
                        season=season,
                        external_id=str(game_id) if game_id else None,
                        week=week_obj,
                        season_type=season_type,
                        home_team=home_team,
                        away_team=away_team,
                        kickoff=new_kickoff,
                        neutral_site=game_data.get('neutralSite', False),
                        conference_game=game_data.get('conferenceGame', False),
                        venue_name=game_data.get('venue', ''),
                        venue_id=game_data.get('venueId'),
                    )
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  ✓ Created: {away_team_name} @ {home_team_name} - "
                            f"{new_kickoff.strftime('%a %m/%d %I:%M %p %Z')}"
                        )
                    )
                else:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  [DRY RUN] Would create: {away_team_name} @ {home_team_name}"
                        )
                    )
                stats['created'] += 1
                continue

            # Check if kickoff time has changed
            kickoff_changed = game.kickoff != new_kickoff
            other_changes = []
            
            # Check other fields that might have changed
            if game.external_id != str(game_id) and game_id:
                other_changes.append('external_id')
            if game.neutral_site != game_data.get('neutralSite', False):
                other_changes.append('neutral_site')
            if game.conference_game != game_data.get('conferenceGame', False):
                other_changes.append('conference_game')
            if game.venue_name != game_data.get('venue', ''):
                other_changes.append('venue_name')
            
            if kickoff_changed or other_changes:
                # Build change message
                changes = []
                if kickoff_changed:
                    old_time = game.kickoff.strftime('%a %m/%d %I:%M %p %Z')
                    new_time = new_kickoff.strftime('%a %m/%d %I:%M %p %Z')
                    changes.append(f"kickoff: {old_time} → {new_time}")
                    
                if other_changes:
                    changes.append(f"fields: {', '.join(other_changes)}")
                
                if not dry_run:
                    # Update the game
                    game.kickoff = new_kickoff
                    if game.external_id != str(game_id) and game_id:
                        game.external_id = str(game_id)
                    game.neutral_site = game_data.get('neutralSite', False)
                    game.conference_game = game_data.get('conferenceGame', False)
                    game.venue_name = game_data.get('venue', '')
                    game.venue_id = game_data.get('venueId')
                    game.save()
                    
                    self.stdout.write(
                        self.style.WARNING(
                            f"  ⟳ Updated: {away_team_name} @ {home_team_name}\n"
                            f"    {' | '.join(changes)}"
                        )
                    )
                else:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  [DRY RUN] Would update: {away_team_name} @ {home_team_name}\n"
                            f"    {' | '.join(changes)}"
                        )
                    )
                
                if kickoff_changed:
                    stats['updated_kickoff'] += 1
                else:
                    stats['updated_other'] += 1
            else:
                stats['no_changes'] += 1

        # Print summary
        self.stdout.write("\n" + "="*60)
        self.stdout.write(self.style.SUCCESS("Update Summary:"))
        self.stdout.write(f"  Total games processed: {stats['total']}")
        self.stdout.write(f"  Games created: {stats['created']}")
        self.stdout.write(
            self.style.WARNING(f"  Kickoff times updated: {stats['updated_kickoff']}")
        )
        self.stdout.write(f"  Other fields updated: {stats['updated_other']}")
        self.stdout.write(f"  No changes needed: {stats['no_changes']}")
        self.stdout.write(f"  Not found in DB: {stats['not_found']}")
        
        if dry_run:
            self.stdout.write("\n" + self.style.WARNING("DRY RUN - No changes were saved"))
        else:
            self.stdout.write("\n" + self.style.SUCCESS("✓ All updates complete!"))

