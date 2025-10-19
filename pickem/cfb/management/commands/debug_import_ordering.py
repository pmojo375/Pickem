"""
Debug script to check col_index ordering between spreads file and database games
"""
import json
from django.core.management.base import BaseCommand
from cfb.models import Week, Season, Team


class Command(BaseCommand):
    help = 'Debug col_index ordering for Week X'

    def add_arguments(self, parser):
        parser.add_argument(
            '--week',
            type=int,
            default=1,
            help='Week number to debug (default: 1)',
        )
        parser.add_argument(
            '--spreads-file',
            type=str,
            help='Path to spreads JSON file',
        )
        parser.add_argument(
            '--season',
            type=int,
            default=2025,
            help='Season year (default: 2025)',
        )

    def handle(self, *args, **options):
        week_num = options['week']
        spreads_file = options['spreads_file']
        season_year = options['season']

        try:
            season = Season.objects.get(year=season_year)
        except Season.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'Season {season_year} not found'))
            return

        try:
            week = Week.objects.get(season=season, number=week_num, season_type='regular')
        except Week.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'Week {week_num} not found'))
            return

        # Get games ordered by kickoff
        games = list(week.games.select_related('home_team', 'away_team').order_by('kickoff'))

        self.stdout.write(self.style.SUCCESS(f'\n=== Database Games (ordered by kickoff) ==='))
        for i, game in enumerate(games):
            self.stdout.write(f'{i}: {game.away_team.name} @ {game.home_team.name} (kickoff: {game.kickoff})')

        if spreads_file:
            self.stdout.write(self.style.SUCCESS(f'\n=== Spreads File Data ==='))
            try:
                with open(spreads_file, 'r') as f:
                    spreads = json.load(f)
                    for spread in spreads[:len(games)]:
                        idx = spread.get('col_index')
                        team_a = spread.get('team_a_abbr') or spread.get('team1_name')
                        team_b = spread.get('team_b_abbr') or spread.get('team2_name')
                        team_a_full = spread.get('team_a_name') or spread.get('team1_name')
                        team_b_full = spread.get('team_b_name') or spread.get('team2_name')
                        
                        self.stdout.write(f'{idx}: {team_a} ({team_a_full}) vs {team_b} ({team_b_full})')
            except FileNotFoundError:
                self.stdout.write(self.style.WARNING(f'Spreads file not found: {spreads_file}'))
            except json.JSONDecodeError:
                self.stdout.write(self.style.WARNING(f'Invalid JSON in spreads file'))

        self.stdout.write(self.style.SUCCESS(f'\n=== Checking Mismatch ==='))
        if spreads_file:
            try:
                with open(spreads_file, 'r') as f:
                    spreads = json.load(f)
                    
                # Build mapping of spreads to games by team name matching
                mismatches = []
                for spread in spreads[:len(games)]:
                    col_idx = spread.get('col_index')
                    
                    # Get team names from spreads (try different formats)
                    team_a_name = spread.get('team_a_name')
                    team_b_name = spread.get('team_b_name')
                    
                    if not team_a_name:
                        # Try looking up from abbreviations if full names aren't in file
                        team_a_abbr = spread.get('team_a_abbr')
                        self.stdout.write(self.style.WARNING(f'  col_index {col_idx}: No full team names in spreads (has abbr: {team_a_abbr})'))
                        continue
                    
                    # Find matching game in database by team names
                    matching_game = None
                    for db_game in games:
                        if ((db_game.home_team.name.lower() in team_a_name.lower() or team_a_name.lower() in db_game.home_team.name.lower()) and
                            (db_game.away_team.name.lower() in team_b_name.lower() or team_b_name.lower() in db_game.away_team.name.lower())) or \
                           ((db_game.home_team.name.lower() in team_b_name.lower() or team_b_name.lower() in db_game.home_team.name.lower()) and
                            (db_game.away_team.name.lower() in team_a_name.lower() or team_a_name.lower() in db_game.away_team.name.lower())):
                            matching_game = db_game
                            break
                    
                    if matching_game:
                        actual_idx = games.index(matching_game)
                        if actual_idx != col_idx:
                            self.stdout.write(self.style.WARNING(
                                f'MISMATCH: col_index {col_idx} in spreads = col_index {actual_idx} in database\n'
                                f'  Spreads: {team_a_name} vs {team_b_name}\n'
                                f'  Database: {matching_game.away_team.name} @ {matching_game.home_team.name}'
                            ))
                            mismatches.append((col_idx, actual_idx, matching_game))
                    else:
                        self.stdout.write(self.style.WARNING(
                            f'NO MATCH FOUND for spreads col_index {col_idx}: {team_a_name} vs {team_b_name}'
                        ))
                
                if not mismatches:
                    self.stdout.write(self.style.SUCCESS('✓ All col_index values match correctly!'))
                else:
                    self.stdout.write(self.style.ERROR(f'\n✗ Found {len(mismatches)} mismatches'))
                    
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'Error: {str(e)}'))
