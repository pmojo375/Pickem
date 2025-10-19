"""
Management command to import spreads and player picks from OPS (other site) data.

Handles:
1. Importing spreads from JSON file and creating GameSpread records
2. Importing player picks from CSV file, creating users if needed
3. Tying picks to league games in the "Spartans" league
"""
import json
import csv
import logging
import unicodedata
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.models import User
from django.db import transaction
from cfb.models import (
    League, Season, Week, Game, Team, GameSpread, LeagueGame, Pick
)

logger = logging.getLogger(__name__)


def normalize_unicode(text):
    """Normalize unicode text to handle accented characters"""
    if not text:
        return text
    return unicodedata.normalize('NFD', text).encode('ascii', 'ignore').decode('ascii')


class Command(BaseCommand):
    help = 'Import spreads and player picks from OPS data files'

    def add_arguments(self, parser):
        parser.add_argument(
            '--spreads',
            type=str,
            required=True,
            help='Path to JSON file containing spread data',
        )
        parser.add_argument(
            '--picks',
            type=str,
            required=True,
            help='Path to CSV file containing player picks',
        )
        parser.add_argument(
            '--season',
            type=int,
            default=2025,
            help='Season year (default: 2025)',
        )
        parser.add_argument(
            '--league',
            type=str,
            default='Spartans',
            help='League name to import picks into (default: Spartans)',
        )
        parser.add_argument(
            '--season-type',
            type=str,
            default='regular',
            help='Season type (regular, postseason, etc. - default: regular)',
        )

    def handle(self, *args, **options):
        spreads_file = options['spreads']
        picks_file = options['picks']
        season_year = options['season']
        league_name = options['league']

        try:
            # Get or verify season exists
            season = Season.objects.get(year=season_year)
            self.stdout.write(
                self.style.SUCCESS(f'Using season: {season}')
            )
        except Season.DoesNotExist:
            raise CommandError(f'Season {season_year} does not exist. Please create it first.')

        # Get or create league
        league, created = League.objects.get_or_create(
            name=league_name,
            defaults={'created_by': User.objects.first()}  # Use first admin/user
        )
        if created:
            self.stdout.write(
                self.style.WARNING(f'Created new league: {league_name}')
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f'Using existing league: {league_name}')
            )

        try:
            # Import spreads first
            self.import_spreads(spreads_file, season)
            
            # Then import picks
            season_type = options['season_type']
            self.import_picks(picks_file, season, league, season_type)
            
            self.stdout.write(
                self.style.SUCCESS('Import completed successfully!')
            )
        except Exception as e:
            raise CommandError(f'Import failed: {str(e)}')

    def import_spreads(self, file_path, season):
        """Import spreads from JSON file"""
        self.stdout.write(f'\nImporting spreads from {file_path}...')
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                spreads_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            raise CommandError(f'Error reading spreads file: {str(e)}')

        if not isinstance(spreads_data, list):
            raise CommandError('Spreads file should contain a JSON array')

        created_count = 0
        error_count = 0

        with transaction.atomic():
            for spread_entry in spreads_data:
                try:
                    col_index = spread_entry.get('col_index')
                    team1_name = spread_entry.get('team1_name')
                    team1_spread = spread_entry.get('team1_spread')
                    team2_name = spread_entry.get('team2_name')
                    team2_spread = spread_entry.get('team2_spread')

                    if not all([col_index is not None, team1_name, team1_spread, team2_name, team2_spread]):
                        self.stdout.write(
                            self.style.WARNING(
                                f'Skipping invalid spread entry at col_index {col_index}'
                            )
                        )
                        error_count += 1
                        continue

                    # Find the teams
                    try:
                        team1 = Team.objects.get(season=season, name__iexact=team1_name)
                        team2 = Team.objects.get(season=season, name__iexact=team2_name)
                    except Team.DoesNotExist:
                        # Try with Unicode normalization
                        normalized_t1 = normalize_unicode(team1_name)
                        normalized_t2 = normalize_unicode(team2_name)
                        team1 = None
                        team2 = None
                        
                        for team in Team.objects.filter(season=season):
                            team_normalized = normalize_unicode(team.name)
                            if team_normalized.lower() == normalized_t1.lower():
                                team1 = team
                            if team_normalized.lower() == normalized_t2.lower():
                                team2 = team
                        
                        if not team1 or not team2:
                            self.stdout.write(
                                self.style.WARNING(
                                    f'Could not find teams for spread: {team1_name} vs {team2_name}'
                                )
                            )
                            error_count += 1
                            continue

                    # Find the game matching these two teams
                    # The col_index will help us later when we need to match picks
                    game = Game.objects.filter(
                        season=season,
                        home_team__in=[team1, team2],
                        away_team__in=[team1, team2]
                    ).first()

                    if not game:
                        self.stdout.write(
                            self.style.WARNING(
                                f'Could not find game for {team1_name} vs {team2_name}'
                            )
                        )
                        error_count += 1
                        continue

                    # Determine home and away spreads
                    if game.home_team == team1:
                        home_spread = team1_spread
                        away_spread = team2_spread
                    else:
                        home_spread = team2_spread
                        away_spread = team1_spread

                    # Create GameSpread record
                    spread, created = GameSpread.objects.update_or_create(
                        game=game,
                        source='OPS Import',
                        defaults={
                            'home_spread': home_spread,
                            'away_spread': away_spread,
                            'week': game.week,
                        }
                    )

                    if created:
                        created_count += 1

                except Exception as e:
                    self.stdout.write(
                        self.style.WARNING(f'Error processing spread entry: {str(e)}')
                    )
                    error_count += 1
                    continue

        self.stdout.write(
            self.style.SUCCESS(
                f'Spreads import completed: {created_count} created/updated, {error_count} errors'
            )
        )

    def import_picks(self, file_path, season, league, season_type):
        """Import picks from CSV file"""
        self.stdout.write(f'\nImporting picks from {file_path}...')

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                picks_data = list(reader)
        except (FileNotFoundError, csv.Error) as e:
            raise CommandError(f'Error reading picks file: {str(e)}')

        if not picks_data:
            raise CommandError('Picks file is empty or invalid')

        created_count = 0
        error_count = 0
        users_created = {}

        with transaction.atomic():
            for pick_row in picks_data:
                try:
                    week_title = pick_row.get('week_title', '').strip()
                    player_name = pick_row.get('player', '').strip()
                    col_index = int(pick_row.get('col_index', -1))
                    picked_team_name = pick_row.get('picked_team_full', '').strip()
                    line_value = float(pick_row.get('line_value', 0))
                    is_key = pick_row.get('is_key', 'False').lower() == 'true'
                    result = pick_row.get('result', '').lower().strip()

                    if not all([player_name, picked_team_name, col_index >= 0]):
                        self.stdout.write(
                            self.style.WARNING(
                                f'Skipping invalid pick entry for {player_name}'
                            )
                        )
                        error_count += 1
                        continue

                    # Get or create user
                    if player_name not in users_created:
                        user, user_created = User.objects.get_or_create(
                            username=player_name.replace(' ', '_').lower(),
                            defaults={
                                'first_name': player_name.split()[0] if ' ' in player_name else player_name,
                                'last_name': ' '.join(player_name.split()[1:]) if ' ' in player_name else '',
                            }
                        )
                        users_created[player_name] = user
                        
                        if user_created:
                            self.stdout.write(
                                self.style.SUCCESS(f'Created user: {player_name}')
                            )
                            # Add user to league if not already a member
                            league.memberships.get_or_create(
                                user=user,
                                defaults={'role': 'member'}
                            )
                    else:
                        user = users_created[player_name]

                    # Find the team
                    try:
                        picked_team = Team.objects.get(season=season, name__iexact=picked_team_name)
                    except Team.DoesNotExist:
                        # Try with Unicode normalization (removes accents)
                        normalized_input = normalize_unicode(picked_team_name)
                        try:
                            # Search for teams where normalized name matches
                            for team in Team.objects.filter(season=season):
                                if normalize_unicode(team.name).lower() == normalized_input.lower():
                                    picked_team = team
                                    break
                            else:
                                raise Team.DoesNotExist
                        except Team.DoesNotExist:
                            self.stdout.write(
                                self.style.WARNING(
                                    f'Could not find team: {picked_team_name} for {player_name}'
                                )
                            )
                            # Show available teams for debugging
                            available_teams = list(
                                Team.objects.filter(season=season).values_list('name', flat=True).distinct()
                            )
                            if available_teams:
                                # Find close matches
                                similar = [t for t in available_teams if picked_team_name.lower() in t.lower() or t.lower() in picked_team_name.lower()]
                                if similar:
                                    self.stdout.write(
                                        self.style.WARNING(
                                            f'  Did you mean? {", ".join(similar)}'
                                        )
                                    )
                            error_count += 1
                            continue

                    # Find the game using col_index and week title
                    # Extract week number from week_title (e.g., "Week 8 Winners" -> 8)
                    week_num_str = ''.join(filter(str.isdigit, week_title.split()[1] if len(week_title.split()) > 1 else ''))
                    
                    if not week_num_str:
                        self.stdout.write(
                            self.style.WARNING(
                                f'Could not parse week number from: {week_title}'
                            )
                        )
                        error_count += 1
                        continue

                    week_num = int(week_num_str)

                    try:
                        # Try to find week with specified season_type
                        week = Week.objects.get(season=season, number=week_num, season_type=season_type)
                    except Week.DoesNotExist:
                        # If specified season_type not found, try any season type
                        try:
                            week = Week.objects.filter(season=season, number=week_num).first()
                            if not week:
                                raise Week.DoesNotExist
                        except Week.DoesNotExist:
                            self.stdout.write(
                                self.style.WARNING(
                                    f'Week {week_num} not found for season {season.year}'
                                )
                            )
                            error_count += 1
                            continue
                    except Exception as e:
                        self.stdout.write(
                            self.style.WARNING(
                                f'Error finding week {week_num}: {str(e)}'
                            )
                        )
                        error_count += 1
                        continue

                    # Find the game by matching the picked_team to a game in this week
                    # This is more reliable than col_index which can vary in ordering
                    game = None
                    week_games = week.games.select_related('home_team', 'away_team').all()
                    
                    picked_team_normalized = normalize_unicode(picked_team.name).lower()
                    
                    for wg in week_games:
                        # Check if picked_team matches either home or away team
                        home_normalized = normalize_unicode(wg.home_team.name).lower()
                        away_normalized = normalize_unicode(wg.away_team.name).lower()
                        
                        if (home_normalized == picked_team_normalized or 
                            away_normalized == picked_team_normalized):
                            game = wg
                            break
                    
                    if not game:
                        self.stdout.write(
                            self.style.WARNING(
                                f'Could not find game for {picked_team.name} in week {week_num} for {player_name}'
                            )
                        )
                        error_count += 1
                        continue

                    # Ensure this game is in the league's games
                    league_game, lg_created = LeagueGame.objects.get_or_create(
                        league=league,
                        game=game,
                    )

                    # Create or update the pick
                    pick, created = Pick.objects.update_or_create(
                        league=league,
                        game=game,
                        user=user,
                        defaults={
                            'picked_team': picked_team,
                            'is_key_pick': is_key,
                        }
                    )

                    if created:
                        created_count += 1

                except Exception as e:
                    self.stdout.write(
                        self.style.WARNING(
                            f'Error processing pick for {player_name}: {str(e)}'
                        )
                    )
                    error_count += 1
                    continue

        self.stdout.write(
            self.style.SUCCESS(
                f'Picks import completed: {created_count} created/updated, {error_count} errors'
            )
        )
