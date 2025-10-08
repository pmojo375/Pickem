"""
Management command to fetch and store poll rankings from CFBD API.
Updates Ranking model for the entire season.
"""
from typing import Dict, Optional

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from cfb.models import Season, Team, Ranking
from cfb.services.cfbd_api import get_cfbd_client


class Command(BaseCommand):
    help = 'Fetch and store poll rankings from CFBD API for an entire season'

    def add_arguments(self, parser):
        parser.add_argument(
            'season',
            type=int,
            help='Season year (e.g., 2025)',
        )
        parser.add_argument(
            '--season-type',
            type=str,
            default='regular',
            choices=['regular', 'postseason'],
            help='Season type (default: regular)',
        )
        parser.add_argument(
            '--week',
            type=int,
            help='Specific week number (optional, default: fetch all weeks)',
        )

    def handle(self, *args, **options):
        season_year = options['season']
        season_type = options['season_type']
        week = options.get('week')
        
        # Verify season exists
        try:
            season = Season.objects.get(year=season_year)
        except Season.DoesNotExist:
            raise CommandError(f'Season {season_year} does not exist')

        if week:
            self.stdout.write(
                self.style.WARNING(
                    f'Fetching rankings from CFBD for {season_year} Week {week} ({season_type})...'
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    f'Fetching all rankings from CFBD for {season_year} season ({season_type})...'
                )
            )

        # Fetch rankings from CFBD
        cfbd_client = get_cfbd_client()
        rankings_data = cfbd_client.fetch_rankings(
            year=season_year,
            week=week,
            season_type=season_type
        )
        
        if not rankings_data:
            raise CommandError('Failed to fetch rankings data from CFBD API')

        self.stdout.write(f'Fetched {len(rankings_data)} weeks of rankings from CFBD')

        # Process each week's rankings
        total_created = 0
        total_updated = 0
        total_skipped = 0

        for week_data in rankings_data:
            created, updated, skipped = self._process_week_rankings(
                week_data,
                season,
                season_type
            )
            total_created += created
            total_updated += updated
            total_skipped += skipped

        # Print summary
        self.stdout.write(
            self.style.SUCCESS(
                f'\nSummary:\n'
                f'  Rankings created: {total_created}\n'
                f'  Rankings updated: {total_updated}\n'
                f'  Rankings skipped (unchanged): {total_skipped}'
            )
        )

    def _process_week_rankings(
        self,
        week_data: Dict,
        season: Season,
        season_type: str
    ) -> tuple:
        """
        Process rankings for a single week.
        
        Returns:
            Tuple of (created_count, updated_count, skipped_count)
        """
        week = week_data.get('week')
        polls = week_data.get('polls', [])

        if not polls:
            self.stdout.write(
                self.style.WARNING(f'  No polls for Week {week}')
            )
            return 0, 0, 0

        created_count = 0
        updated_count = 0
        skipped_count = 0

        for poll_data in polls:
            poll_name = poll_data.get('poll')
            ranks = poll_data.get('ranks', [])

            self.stdout.write(f'  Week {week} - {poll_name}: {len(ranks)} teams')

            for rank_data in ranks:
                result = self._process_team_ranking(
                    season=season,
                    week=week,
                    season_type=season_type,
                    poll_name=poll_name,
                    rank_data=rank_data
                )
                
                if result == 'created':
                    created_count += 1
                elif result == 'updated':
                    updated_count += 1
                elif result == 'skipped':
                    skipped_count += 1

        return created_count, updated_count, skipped_count

    def _process_team_ranking(
        self,
        season: Season,
        week: int,
        season_type: str,
        poll_name: str,
        rank_data: Dict
    ) -> str:
        """
        Process a single team's ranking.
        
        Returns:
            'created', 'updated', 'skipped', or 'error'
        """
        school_name = rank_data.get('school')
        team_id = rank_data.get('teamId')
        rank = rank_data.get('rank')
        first_place_votes = rank_data.get('firstPlaceVotes', 0)
        points = rank_data.get('points', 0)

        # Find the team in our database
        team = self._find_team(season, school_name, team_id)
        
        if not team:
            self.stdout.write(
                self.style.WARNING(
                    f'    Team not found in DB: {school_name} (ID: {team_id})'
                )
            )
            return 'error'

        # Check if ranking already exists
        try:
            existing_ranking = Ranking.objects.get(
                season=season,
                week=week,
                season_type=season_type,
                team=team,
                poll=poll_name
            )
            
            # Check if anything changed
            if (existing_ranking.rank == rank and
                existing_ranking.first_place_votes == first_place_votes and
                existing_ranking.points == points):
                return 'skipped'
            
            # Update the ranking
            existing_ranking.rank = rank
            existing_ranking.first_place_votes = first_place_votes
            existing_ranking.points = points
            existing_ranking.save()
            
            return 'updated'
            
        except Ranking.DoesNotExist:
            # Create new ranking
            Ranking.objects.create(
                season=season,
                week=week,
                season_type=season_type,
                team=team,
                poll=poll_name,
                rank=rank,
                first_place_votes=first_place_votes,
                points=points
            )
            
            return 'created'

    def _find_team(
        self,
        season: Season,
        school_name: str,
        team_id: Optional[int]
    ) -> Optional[Team]:
        """
        Find a team in the database by name or CFBD ID.
        """
        # First try by CFBD ID if available
        if team_id:
            team = Team.objects.filter(
                season=season,
                cfbd_id=team_id
            ).first()
            
            if team:
                return team
        
        # Try exact name match
        team = Team.objects.filter(
            season=season,
            name__iexact=school_name
        ).first()
        
        if team:
            return team
        
        # Try fuzzy matching
        teams = Team.objects.filter(season=season)
        
        for t in teams:
            if self._teams_match(t.name, school_name):
                return t
        
        return None

    def _teams_match(self, db_name: str, api_name: str) -> bool:
        """
        Check if team names match with fuzzy logic.
        """
        db_normalized = db_name.lower().strip()
        api_normalized = api_name.lower().strip()
        
        # Exact match
        if db_normalized == api_normalized:
            return True
        
        # One contains the other
        if db_normalized in api_normalized or api_normalized in db_normalized:
            return True
        
        # Check word overlap
        db_words = set(db_normalized.split())
        api_words = set(api_normalized.split())
        common_words = db_words & api_words
        
        # At least 1 word in common for short names, 2 for longer names
        if len(db_words) <= 2 or len(api_words) <= 2:
            return len(common_words) >= 1
        return len(common_words) >= 2

