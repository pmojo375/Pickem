/**
 * Live Score Updater
 * 
 * Automatically polls /api/games endpoint and updates scores in real-time.
 * Features:
 * - Dynamic polling intervals based on game state
 * - Page Visibility API for tab detection
 * - Smooth animations for score changes
 * - Exponential backoff on errors
 * - Preserves scroll position and user interactions
 * 
 * @version 1.0.0
 */

class LiveScoreUpdater {
    constructor(options = {}) {
        // Configuration
        this.config = {
            // API endpoint
            apiEndpoint: options.apiEndpoint || '/api/games/',
            
            // Polling intervals (milliseconds)
            intervals: {
                live: options.livePollInterval || 5000,      // 5 seconds when games are live
                normal: options.normalPollInterval || 30000, // 30 seconds when all games final/pregame
                hidden: options.hiddenPollInterval || 60000  // 60 seconds when tab is hidden
            },
            
            // Error handling
            maxRetries: options.maxRetries || 5,
            baseRetryDelay: options.baseRetryDelay || 2000, // 2 seconds base delay
            maxRetryDelay: options.maxRetryDelay || 60000,  // 60 seconds max delay
            
            // Animation settings
            scoreChangeAnimation: options.scoreChangeAnimation || 'score-pulse',
            highlightDuration: options.highlightDuration || 2000, // 2 seconds
            
            // Callbacks
            onUpdate: options.onUpdate || null,
            onError: options.onError || null,
            onStatusChange: options.onStatusChange || null
        };
        
        // State
        this.state = {
            isPolling: false,
            isPaused: false,
            isTabVisible: true,
            hasLiveGames: false,
            currentInterval: this.config.intervals.normal,
            gamesData: new Map(), // Store games by ID
            pollTimer: null,
            retryCount: 0,
            lastSuccessfulPoll: null,
            lastError: null
        };
        
        // Bind methods
        this.handleVisibilityChange = this.handleVisibilityChange.bind(this);
        this.poll = this.poll.bind(this);
        
        // Initialize
        this.init();
    }
    
    /**
     * Initialize the updater
     */
    init() {
        // Set up Page Visibility API
        document.addEventListener('visibilitychange', this.handleVisibilityChange);
        
        // Check initial visibility state
        this.state.isTabVisible = !document.hidden;
        
        console.log('[LiveScoreUpdater] Initialized with config:', this.config);
    }
    
    /**
     * Start polling
     */
    start() {
        if (this.state.isPolling) {
            console.warn('[LiveScoreUpdater] Already polling');
            return;
        }
        
        console.log('[LiveScoreUpdater] Starting auto-update');
        this.state.isPolling = true;
        this.state.isPaused = false;
        
        // Initial poll immediately
        this.poll();
    }
    
    /**
     * Stop polling
     */
    stop() {
        console.log('[LiveScoreUpdater] Stopping auto-update');
        this.state.isPolling = false;
        
        if (this.state.pollTimer) {
            clearTimeout(this.state.pollTimer);
            this.state.pollTimer = null;
        }
    }
    
    /**
     * Pause polling (useful for user interactions)
     */
    pause() {
        this.state.isPaused = true;
        console.log('[LiveScoreUpdater] Paused');
    }
    
    /**
     * Resume polling
     */
    resume() {
        if (this.state.isPaused) {
            this.state.isPaused = false;
            console.log('[LiveScoreUpdater] Resumed');
            this.scheduleNextPoll();
        }
    }
    
    /**
     * Handle page visibility changes
     */
    handleVisibilityChange() {
        this.state.isTabVisible = !document.hidden;
        
        if (this.state.isTabVisible) {
            console.log('[LiveScoreUpdater] Tab visible - resuming active polling');
            if (this.state.isPolling && !this.state.isPaused) {
                // Poll immediately when tab becomes visible
                this.poll();
            }
        } else {
            console.log('[LiveScoreUpdater] Tab hidden - reducing poll frequency');
            this.scheduleNextPoll();
        }
    }
    
    /**
     * Calculate current polling interval based on state
     */
    getCurrentInterval() {
        // If tab is hidden, use longer interval
        if (!this.state.isTabVisible) {
            return this.config.intervals.hidden;
        }
        
        // If any games are live, use fast polling
        if (this.state.hasLiveGames) {
            return this.config.intervals.live;
        }
        
        // Otherwise use normal interval
        return this.config.intervals.normal;
    }
    
    /**
     * Schedule next poll
     */
    scheduleNextPoll() {
        if (!this.state.isPolling || this.state.isPaused) {
            return;
        }
        
        // Clear existing timer
        if (this.state.pollTimer) {
            clearTimeout(this.state.pollTimer);
        }
        
        this.state.currentInterval = this.getCurrentInterval();
        
        // Next poll scheduled
        
        this.state.pollTimer = setTimeout(() => {
            this.poll();
        }, this.state.currentInterval);
    }
    
    /**
     * Main polling function
     */
    async poll() {
        if (!this.state.isPolling || this.state.isPaused) {
            return;
        }
        
        try {
            // Get the game IDs that are actually on the page
            const gameElements = document.querySelectorAll('[data-game-id]');
            const gameIds = [...new Set(
                Array.from(gameElements).map(el => el.getAttribute('data-game-id'))
            )];
            
            if (gameIds.length === 0) {
                console.warn('[LiveScores] No games on page, skipping poll');
                this.scheduleNextPoll();
                return;
            }
            
            // Ask for the games rendered on this page. Fetching the first 500
            // games in kickoff order can omit games later in the season.
            const query = new URLSearchParams({
                ids: gameIds.join(','),
                limit: String(gameIds.length)
            });
            const url = `${this.config.apiEndpoint}?${query.toString()}`;
            
            // Create abort controller for manual timeout (better browser compatibility)
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 10000); // 10 second timeout
            
            const response = await fetch(url, {
                method: 'GET',
                headers: {
                    'Accept': 'application/json',
                },
                signal: controller.signal
            });
            
            // Clear timeout if request succeeds
            clearTimeout(timeoutId);
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const data = await response.json();
            
            // Filter to only games that are on the page
            const gameIdsOnPage = new Set(gameIds);
            const relevantGames = data.games.filter(game => gameIdsOnPage.has(String(game.id)));
            
            // Process only the relevant games
            this.processGamesData({
                ...data,
                games: relevantGames
            });
            
            // Reset retry count on success
            this.state.retryCount = 0;
            this.state.lastSuccessfulPoll = new Date();
            this.state.lastError = null;
            
            // Schedule next poll
            this.scheduleNextPoll();
            
        } catch (error) {
            console.error('[LiveScoreUpdater] Poll failed:', error);
            this.handlePollError(error);
        }
    }
    
    /**
     * Process games data from API
     */
    processGamesData(data) {
        const games = data.games || [];
        let liveGameCount = 0;
        let updatedGames = [];
        
        games.forEach(game => {
            // Check if game is live
            const isLive = this.isGameLive(game);
            if (isLive) {
                liveGameCount++;
            }
            
            // Check if this is a new game or if it has changed
            const existingGame = this.state.gamesData.get(game.id);
            
            if (!existingGame) {
                // First sight of this game: store state, but do NOT rebuild the status
                // block. SSR already has quarter/clock/down-distance; a full
                // updateGameStatus here was wiping down/distance a second after load
                // (especially with a stale cached live-score-updater.js).
                this.state.gamesData.set(game.id, game);

                const initialChanges = [];
                if (game.possession) {
                    initialChanges.push({ type: 'possession', old: null, new: game.possession });
                }
                // Only patch down/distance when the API has a value; never clear SSR
                // on first poll if the API field is empty/missing.
                if (game.down_distance_text || game.ball_on) {
                    initialChanges.push({
                        type: 'down_distance',
                        old: null,
                        new: game.down_distance_text || ''
                    });
                }
                if (game.is_final) {
                    initialChanges.push({ type: 'is_final', old: false, new: true });
                }
                if (initialChanges.length > 0) {
                    this.updateGameUI(game, initialChanges);
                }

                // If kickoff has passed and scores are still null, upgrade "—" → "0"
                if (
                    (game.home_score === null || game.away_score === null) &&
                    this.isGameStarted(game)
                ) {
                    const gameElement = document.querySelector(`[data-game-id="${game.id}"]`);
                    if (gameElement) {
                        if (game.home_score === null) {
                            this.updateScore(gameElement, 'home', game.home_score, game);
                        }
                        if (game.away_score === null) {
                            this.updateScore(gameElement, 'away', game.away_score, game);
                        }
                    }
                }
            } else {
                // Check what changed
                const changes = this.detectChanges(existingGame, game);
                
                if (changes.length > 0) {
                    console.log(`[LiveScores] ${game.away_team.abbreviation || game.away_team.name} @ ${game.home_team.abbreviation || game.home_team.name} updated`);
                    
                    // Update stored game data
                    this.state.gamesData.set(game.id, game);
                    
                    // Update UI
                    this.updateGameUI(game, changes);
                    
                    updatedGames.push({ game, changes });
                } else if (
                    (game.home_score === null || game.away_score === null) &&
                    this.isGameStarted(game)
                ) {
                    // Kickoff may have passed with scores still null — refresh 0 vs —
                    const gameElement = document.querySelector(`[data-game-id="${game.id}"]`);
                    if (gameElement) {
                        if (game.home_score === null) {
                            this.updateScore(gameElement, 'home', game.home_score, game);
                        }
                        if (game.away_score === null) {
                            this.updateScore(gameElement, 'away', game.away_score, game);
                        }
                    }
                }
            }

            // Keep the user's provisional ATS result in sync with every poll.
            // This is intentionally separate from final pick grading.
            const gameElement = document.querySelector(`[data-game-id="${game.id}"]`);
            if (gameElement) {
                this.updateLiveCoverIndicator(gameElement, game);
            }
        });
        
        // Update live game state
        const hadLiveGames = this.state.hasLiveGames;
        this.state.hasLiveGames = liveGameCount > 0;
        
        if (hadLiveGames !== this.state.hasLiveGames) {
            console.log(`[LiveScoreUpdater] Live game state changed: ${liveGameCount} live games`);
            
            if (this.config.onStatusChange) {
                this.config.onStatusChange({
                    hasLiveGames: this.state.hasLiveGames,
                    liveGameCount
                });
            }
        }
        
        // Call update callback if provided
        if (this.config.onUpdate && updatedGames.length > 0) {
            this.config.onUpdate(updatedGames);
        }
        
        // Update status indicator
        this.updateStatusIndicator(liveGameCount);
    }
    
    /**
     * Check if a game is currently live
     */
    isGameLive(game) {
        return !game.is_final && game.quarter > 0;
    }

    /**
     * Check if a game has started (kickoff passed or quarter set).
     * Matches server-side has_started: any non-null quarter counts.
     */
    isGameStarted(game) {
        if (!game) {
            return false;
        }
        if (game.quarter != null) {
            return true;
        }
        if (game.kickoff) {
            const kickoff = new Date(game.kickoff);
            if (!Number.isNaN(kickoff.getTime())) {
                return kickoff <= new Date();
            }
        }
        return false;
    }
    
    /**
     * Detect what changed between two game states
     */
    detectChanges(oldGame, newGame) {
        const changes = [];
        
        // Check score changes
        if (oldGame.home_score !== newGame.home_score) {
            changes.push({
                type: 'home_score',
                old: oldGame.home_score,
                new: newGame.home_score
            });
        }
        
        if (oldGame.away_score !== newGame.away_score) {
            changes.push({
                type: 'away_score',
                old: oldGame.away_score,
                new: newGame.away_score
            });
        }
        
        // Check quarter/period changes
        if (oldGame.quarter !== newGame.quarter) {
            changes.push({
                type: 'quarter',
                old: oldGame.quarter,
                new: newGame.quarter
            });
        }
        
        // Check clock changes - normalize empty string and null
        const oldClock = oldGame.clock || '';
        const newClock = newGame.clock || '';
        if (oldClock !== newClock) {
            changes.push({
                type: 'clock',
                old: oldGame.clock,
                new: newGame.clock
            });
        }
        
        // Check final status
        if (oldGame.is_final !== newGame.is_final) {
            changes.push({
                type: 'is_final',
                old: oldGame.is_final,
                new: newGame.is_final
            });
        }

        // Possession (home / away / empty)
        const oldPoss = oldGame.possession || '';
        const newPoss = newGame.possession || '';
        if (oldPoss !== newPoss) {
            changes.push({
                type: 'possession',
                old: oldPoss,
                new: newPoss
            });
        }

        // Down & distance / ball spot
        const oldDown = oldGame.down_distance_text || '';
        const newDown = newGame.down_distance_text || '';
        const oldBall = oldGame.ball_on || '';
        const newBall = newGame.ball_on || '';
        if (oldDown !== newDown || oldBall !== newBall) {
            changes.push({
                type: 'down_distance',
                old: oldDown,
                new: newDown
            });
        }
        
        return changes;
    }
    
    /**
     * Update game UI with new data
     */
    updateGameUI(game, changes) {
        // Find game element by data-game-id attribute
        const gameElement = document.querySelector(`[data-game-id="${game.id}"]`);
        
        if (!gameElement) {
            console.warn(`[LiveScores] Game element not found for ID ${game.id}`);
            return;
        }
        
        // Save scroll position
        const scrollY = window.scrollY;

        changes.forEach(change => {
            switch (change.type) {
                case 'home_score':
                    this.updateScore(gameElement, 'home', game.home_score, game);
                    break;
                    
                case 'away_score':
                    this.updateScore(gameElement, 'away', game.away_score, game);
                    break;
                    
                case 'quarter':
                case 'clock':
                    this.updateGameStatus(gameElement, game);
                    break;
                    
                case 'is_final':
                    this.updateGameStatus(gameElement, game);
                    this.updateGameFinalIndicators(gameElement, game);
                    this.updatePossession(gameElement, game);
                    this.updateDownDistance(gameElement, game);
                    break;

                case 'possession':
                    this.updatePossession(gameElement, game);
                    break;

                case 'down_distance':
                    this.updateDownDistance(gameElement, game);
                    break;
            }
        });
        
        // Graded colors, cover marks, and the winner trophy paint on the card.
        if (game.is_final) {
            this.applyGradedResult(gameElement, game);
        }

        // Restore scroll position
        window.scrollTo(0, scrollY);
    }

    /**
     * Whole-number spreads pick up a half point when the league forces hooks.
     * Matches cfb.services.hooks.apply_forced_hook.
     */
    applyForcedHook(spread) {
        if (!Number.isFinite(spread) || spread === 0 || !Number.isInteger(spread)) {
            return spread;
        }
        return spread > 0 ? spread + 0.5 : spread - 0.5;
    }

    /**
     * Whether this side covered the locked spread. Same rule as the
     * team_covered_spread template tag (hooks are not applied here).
     * Returns true, false, or null when there is no spread.
     */
    teamCovered(side, homeScore, awayScore, spread) {
        if (!Number.isFinite(spread)) {
            return null;
        }
        const homeCovered = (homeScore - awayScore) > -spread;
        if (side === 'home') {
            return homeCovered;
        }
        if (side === 'away') {
            return !homeCovered;
        }
        return null;
    }

    /**
     * Whether the user's pick is correct. Matches is_pick_correct:
     * against-the-spread with optional hooks, otherwise straight-up.
     * Returns true, false, or null for a push / tie.
     */
    gradePick(pickedSide, homeScore, awayScore, spread, atsEnabled, forceHooks) {
        const margin = homeScore - awayScore;
        if (!atsEnabled) {
            if (margin === 0) {
                return null;
            }
            if (pickedSide === 'home') {
                return margin > 0;
            }
            if (pickedSide === 'away') {
                return margin < 0;
            }
            return null;
        }
        if (!Number.isFinite(spread)) {
            return null;
        }
        const gradedSpread = forceHooks ? this.applyForcedHook(spread) : spread;
        if (!forceHooks && Math.abs(margin - (-gradedSpread)) < 1e-9) {
            return null;
        }
        const homeCovered = margin > -gradedSpread;
        if (pickedSide === 'home') {
            return homeCovered;
        }
        if (pickedSide === 'away') {
            return !homeCovered;
        }
        return null;
    }

    /**
     * Paint the same final-result styling a reload would render:
     * green ring on a correct pick, red ring on the covering opponent,
     * cover check / x, and a trophy on the winner.
     */
    applyGradedResult(gameElement, game) {
        if (!gameElement || !game || !game.is_final) {
            return;
        }
        const homeScore = Number(game.home_score);
        const awayScore = Number(game.away_score);
        if (!Number.isFinite(homeScore) || !Number.isFinite(awayScore)) {
            return;
        }

        const container = gameElement.closest('#games-container');
        const atsEnabled = !container || container.getAttribute('data-ats-enabled') !== 'false';
        const forceHooks = !!(container && container.getAttribute('data-force-hooks') === 'true');
        const pickedSide = gameElement.getAttribute('data-picked-side');
        const spreadRaw = gameElement.getAttribute('data-locked-home-spread');
        const hasSpread = spreadRaw !== null && spreadRaw !== '';
        const spread = hasSpread ? Number(spreadRaw) : NaN;
        const isCorrect = this.gradePick(
            pickedSide, homeScore, awayScore, spread, atsEnabled, forceHooks
        );

        ['away', 'home'].forEach(side => {
            const teamElement = gameElement.querySelector(`[data-team-side="${side}"]`);
            if (!teamElement) {
                return;
            }
            const covered = hasSpread ? this.teamCovered(side, homeScore, awayScore, spread) : null;
            this.paintTeamResult(teamElement, {
                picked: side === pickedSide,
                correct: isCorrect === true && side === pickedSide,
                opponentCovered: covered === true && side !== pickedSide,
                covered: covered,
                won: side === 'home' ? homeScore > awayScore : awayScore > homeScore
            });
        });
    }

    paintTeamResult(teamElement, state) {
        teamElement.classList.remove(
            'bg-success/10', 'bg-error/10', 'bg-primary/20', 'bg-base-200',
            'ring-2', 'ring-4', 'ring-green-500', 'ring-red-500', 'ring-primary',
            'z-10', 'z-20'
        );
        if (state.correct) {
            teamElement.classList.add('bg-success/10', 'ring-4', 'ring-green-500', 'z-20');
        } else if (state.opponentCovered) {
            teamElement.classList.add('bg-error/10', 'ring-4', 'ring-red-500', 'z-20');
        } else if (state.picked) {
            teamElement.classList.add('bg-primary/20', 'ring-2', 'ring-primary', 'z-10');
        } else {
            teamElement.classList.add('bg-base-200');
        }

        const nameRow = teamElement.querySelector('[data-team-info] > .flex');
        if (nameRow) {
            let trophy = nameRow.querySelector('[data-winner-trophy]');
            if (!trophy) {
                const existing = nameRow.querySelector('.fa-trophy');
                if (existing) {
                    existing.setAttribute('data-winner-trophy', '');
                    trophy = existing;
                }
            }
            if (state.won) {
                if (!trophy) {
                    trophy = document.createElement('i');
                    trophy.className = 'fas fa-trophy text-success text-xs';
                    trophy.title = 'Winner';
                    trophy.setAttribute('data-winner-trophy', '');
                    nameRow.appendChild(trophy);
                }
            } else if (trophy) {
                trophy.remove();
            }
        }

        const ghost = teamElement.querySelector('.badge-ghost');
        const row = ghost ? ghost.parentElement : null;
        if (!row) {
            return;
        }
        let badge = row.querySelector('[data-cover-badge]');
        if (!badge) {
            const existingBadge = row.querySelector('.badge-success, .badge-error');
            if (existingBadge) {
                existingBadge.setAttribute('data-cover-badge', '');
                badge = existingBadge;
            }
        }
        if (state.covered == null) {
            if (badge) {
                badge.remove();
            }
            return;
        }
        if (!badge) {
            badge = document.createElement('div');
            badge.setAttribute('data-cover-badge', '');
            row.appendChild(badge);
        }
        const covered = state.covered === true;
        badge.className = covered ? 'badge badge-success badge-xs' : 'badge badge-error badge-xs';
        badge.title = covered ? 'Covered the spread' : 'Did not cover';
        badge.textContent = '';
        const icon = document.createElement('i');
        icon.className = covered ? 'fas fa-check text-[8px]' : 'fas fa-times text-[8px]';
        badge.appendChild(icon);
    }
    
    /**
     * While a game is live, stripe the team that is covering.
     * Green stripes when that team is the user's pick, red stripes otherwise.
     * The purple pick highlight stays in place either way.
     */
    updateLiveCoverIndicator(gameElement, game) {
        const pickedSide = gameElement.getAttribute('data-picked-side');
        const spreadValue = gameElement.getAttribute('data-locked-home-spread');
        const teamElements = gameElement.querySelectorAll('[data-team-side]');

        // Always clear stale live state first (including when a game becomes final).
        teamElements.forEach(teamElement => {
            teamElement.classList.remove('live-covering', 'live-opponent-covering', 'live-not-covering', 'live-push');
            const oldBadge = teamElement.querySelector('[data-live-cover-status]');
            if (oldBadge) {
                oldBadge.remove();
            }
        });

        if (
            game.is_final ||
            !this.isGameLive(game) ||
            spreadValue === '' ||
            game.home_score === null || game.home_score === undefined ||
            game.away_score === null || game.away_score === undefined
        ) {
            return;
        }

        const spread = Number(spreadValue);
        const homeScore = Number(game.home_score);
        const awayScore = Number(game.away_score);
        if (![spread, homeScore, awayScore].every(Number.isFinite)) {
            return;
        }

        // Same convention used by server-side pick grading:
        // home covers when (home - away) > -homeSpread. A push has no covering team.
        const atsMargin = (homeScore - awayScore) + spread;
        const coveringSide = atsMargin > 0 ? 'home' : (atsMargin < 0 ? 'away' : '');
        const coveringElement = coveringSide
            ? gameElement.querySelector(`[data-team-side="${coveringSide}"]`)
            : null;
        if (!coveringElement) {
            return;
        }

        coveringElement.classList.add(
            coveringSide === pickedSide ? 'live-covering' : 'live-opponent-covering'
        );

        const badge = document.createElement('span');
        badge.setAttribute('data-live-cover-status', '');
        badge.className = 'live-cover-status';
        badge.textContent = 'COVERING';
        badge.title = 'Covering the locked spread at the current score';
        // Info column keeps the chip under the team name, clear of the score
        // on short mobile rows.
        const info = coveringElement.querySelector('[data-team-info]');
        (info || coveringElement).appendChild(badge);
    }

    /**
     * Show/hide football icon for which team has possession
     */
    updatePossession(gameElement, game) {
        const possession = (!game.is_final && game.possession) ? game.possession : '';
        gameElement.querySelectorAll('[data-possession-icon]').forEach(icon => {
            const side = icon.getAttribute('data-possession-icon');
            icon.classList.toggle('hidden', side !== possession);
        });
    }

    /**
     * Update live down & distance (e.g. "3rd & 5 · MSST 30")
     */
    updateDownDistance(gameElement, game) {
        const statusElement = gameElement.querySelector('[data-game-status]');
        if (!statusElement) {
            return;
        }

        let el = statusElement.querySelector('[data-down-distance]');
        const downText = (!game.is_final && game.down_distance_text) ? game.down_distance_text : '';
        const ballOn = (!game.is_final && game.ball_on) ? game.ball_on : '';

        if (!downText) {
            // Final → clear. Otherwise keep the last shown down/distance so a
            // status rebuild / empty poll cannot blank the SSR text.
            if (game.is_final && el) {
                el.textContent = '';
                el.classList.add('hidden');
                el.removeAttribute('title');
            }
            return;
        }

        if (!el) {
            // Insert under the clock when the status block was rebuilt without it
            el = document.createElement('div');
            el.setAttribute('data-down-distance', '');
            el.className = 'text-xs font-bold text-warning/90 mt-1';
            const clockEl = statusElement.querySelector('.text-sm.md\\:text-base.font-semibold.text-warning');
            const center = statusElement.querySelector('.text-center');
            if (clockEl && clockEl.parentElement) {
                clockEl.insertAdjacentElement('afterend', el);
            } else if (center) {
                center.appendChild(el);
            } else {
                statusElement.appendChild(el);
            }
        }

        el.className = 'text-xs font-bold text-warning/90 mt-1';
        el.textContent = '';
        el.appendChild(document.createTextNode(downText));
        if (ballOn) {
            const spot = document.createElement('span');
            spot.className = 'opacity-70 font-semibold';
            spot.textContent = ` · ${ballOn}`;
            el.appendChild(spot);
        }
        el.title = ballOn ? `${downText} at ${ballOn}` : downText;
        el.classList.remove('hidden');
    }

    /**
     * Resolve a displayable numeric score, or null to leave the DOM alone.
     * JS never writes dashes — those only come from server-side render.
     */
    formatScore(score, game) {
        if (typeof score === 'number' && Number.isFinite(score)) {
            return String(score);
        }
        if (typeof score === 'string' && score.trim() !== '') {
            const asNum = Number(score);
            if (Number.isFinite(asNum)) {
                return String(asNum);
            }
        }
        // Missing score after kickoff → show 0; pregame → don't touch SSR
        if ((score === null || score === undefined || score === '') && this.isGameStarted(game)) {
            return '0';
        }
        return null;
    }

    /**
     * Update score display with animation
     */
    updateScore(gameElement, team, newScore, game = null) {
        const scoreSelector = `[data-score="${team}"]`;
        const scoreElement = gameElement.querySelector(scoreSelector);
        
        if (!scoreElement) {
            return;
        }
        
        const display = this.formatScore(newScore, game);
        // null means "leave whatever the server rendered"
        if (display === null) {
            return;
        }

        const current = (scoreElement.textContent || '').trim();
        // Never replace a digit with a dash/placeholder from any code path
        if (/^[\u2014\u2013\-]$/.test(display) && /^\d+$/.test(current)) {
            return;
        }

        if (current === display) {
            return;
        }

        scoreElement.textContent = display;
        
        scoreElement.classList.remove('score-pulse');
        void scoreElement.offsetWidth;
        scoreElement.classList.add('score-pulse');
        
        setTimeout(() => {
            scoreElement.classList.remove('score-pulse');
        }, this.config.highlightDuration);
    }
    
    /**
     * Update game status (quarter, clock, final)
     */
    updateGameStatus(gameElement, game) {
        const statusElement = gameElement.querySelector('[data-game-status]');
        
        if (!statusElement) {
            console.warn(`[LiveScoreUpdater] Status element not found`);
            return;
        }
        
        // Get the kickoff time from the page (already formatted)
        // Try to extract from any existing time display
        let kickoffTimeText = '';
        
        // Look for the main time text (not the "Scheduled" label)
        // Try multiple selectors since the class varies by game state
        const timeSelectors = [
            '.text-base.md\\:text-lg',  // Scheduled games
            '.text-xs.text-base-content\\/60'  // Final/Live games (below status)
        ];
        
        for (const selector of timeSelectors) {
            const timeElement = statusElement.querySelector(selector);
            if (timeElement && timeElement.textContent && !timeElement.textContent.includes('Scheduled')) {
                kickoffTimeText = timeElement.textContent.trim();
                break;
            }
        }
        
        // Fallback: if no time found, look for ANY text that looks like a date/time
        if (!kickoffTimeText) {
            const allText = statusElement.textContent;
            const timeMatch = allText.match(/(Mon|Tue|Wed|Thu|Fri|Sat|Sun),.*?ET/);
            if (timeMatch) {
                kickoffTimeText = timeMatch[0];
            }
        }
        
        // IMPORTANT: Preserve the spread badge HTML if it exists
        let spreadBadgeHTML = '';
        const spreadContainer = statusElement.querySelector('.text-center.mt-2');
        if (spreadContainer) {
            // Save the entire spread container (including the mt-2 wrapper)
            spreadBadgeHTML = spreadContainer.outerHTML;
        }

        // Keep SSR / prior down-distance if this rebuild would otherwise drop it
        // (stale JS build, or API briefly missing the field).
        let preservedDownHTML = '';
        const existingDown = statusElement.querySelector('[data-down-distance]');
        if (
            existingDown &&
            !existingDown.classList.contains('hidden') &&
            (existingDown.textContent || '').trim()
        ) {
            preservedDownHTML = existingDown.outerHTML;
        }
        
        let statusHTML = '';
        
        if (game.is_final) {
            statusHTML = `
<div class="text-center">
    <div class="text-2xl md:text-3xl font-bold text-success mb-1">FINAL</div>
    ${kickoffTimeText ? `<div class="text-xs text-base-content/60">${kickoffTimeText}</div>` : ''}
</div>
${spreadBadgeHTML}`;
        } else if (game.quarter != null && game.quarter !== '') {
            const downText = game.down_distance_text || '';
            const ballOn = game.ball_on || '';
            let downHTML;
            if (downText) {
                downHTML = `<div class="text-xs font-bold text-warning/90 mt-1" data-down-distance title="${ballOn ? `${downText} at ${ballOn}` : downText}">${downText}${ballOn ? `<span class="opacity-70 font-semibold"> · ${ballOn}</span>` : ''}</div>`;
            } else if (preservedDownHTML) {
                downHTML = preservedDownHTML;
            } else {
                downHTML = `<div class="text-xs font-bold text-warning/90 mt-1 hidden" data-down-distance></div>`;
            }
            statusHTML = `
<div class="text-center">
    <div class="text-xl md:text-2xl font-bold text-warning animate-pulse mb-1">
        Q${game.quarter}
    </div>
    <div class="text-sm md:text-base font-semibold text-warning">${game.clock || ''}</div>
    ${downHTML}
    ${kickoffTimeText ? `<div class="text-xs text-base-content/60 mt-1">${kickoffTimeText}</div>` : ''}
</div>
${spreadBadgeHTML}`;
        } else {
            statusHTML = `
<div class="text-center">
    ${kickoffTimeText ? `<div class="text-base md:text-lg font-semibold text-base-content/70 mb-1">${kickoffTimeText}</div>` : ''}
    <div class="text-xs text-base-content/50">Scheduled</div>
</div>
${spreadBadgeHTML}`;
        }
        
        statusElement.innerHTML = statusHTML;
    }
    
    /**
     * Mark the card final and paint the graded result in place.
     */
    updateGameFinalIndicators(gameElement, game) {
        gameElement.classList.add('game-final');
        this.applyGradedResult(gameElement, game);
    }

    /**
     * Kept so an older cached script that still calls this does not throw.
     * The graded result is drawn on the card, so there is nothing to prompt.
     */
    showGameFinalNotification() {}
    
    /**
     * Update status indicator in header
     */
    updateStatusIndicator(liveGameCount) {
        const indicator = document.getElementById('live-status-indicator');
        
        if (!indicator) {
            return;
        }
        
        if (liveGameCount > 0) {
            indicator.innerHTML = `
                <div class="flex items-center gap-2">
                    <span class="relative flex h-3 w-3">
                        <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-success opacity-75"></span>
                        <span class="relative inline-flex rounded-full h-3 w-3 bg-success"></span>
                    </span>
                    <span class="text-sm font-medium">${liveGameCount} Live</span>
                </div>
            `;
        } else {
            const nextUpdate = Math.round(this.state.currentInterval / 1000);
            indicator.innerHTML = `
                <div class="flex items-center gap-2">
                    <i class="fas fa-clock text-base-content/50"></i>
                    <span class="text-sm text-base-content/70">Next update: ${nextUpdate}s</span>
                </div>
            `;
        }
    }
    
    /**
     * Handle polling errors with exponential backoff
     */
    handlePollError(error) {
        this.state.retryCount++;
        this.state.lastError = error;
        
        if (this.config.onError) {
            this.config.onError(error, this.state.retryCount);
        }
        
        if (this.state.retryCount >= this.config.maxRetries) {
            console.error('[LiveScoreUpdater] Max retries reached, stopping updates');
            this.showErrorMessage('Unable to fetch live scores. Please refresh the page.');
            this.stop();
            return;
        }
        
        // Calculate exponential backoff delay
        const baseDelay = this.config.baseRetryDelay;
        const exponentialDelay = baseDelay * Math.pow(2, this.state.retryCount - 1);
        const jitter = Math.random() * 1000; // Add jitter to avoid thundering herd
        const retryDelay = Math.min(exponentialDelay + jitter, this.config.maxRetryDelay);
        
        // Schedule retry
        if (this.state.pollTimer) {
            clearTimeout(this.state.pollTimer);
        }
        
        this.state.pollTimer = setTimeout(() => {
            this.poll();
        }, retryDelay);
    }
    
    /**
     * Show error message to user
     */
    showErrorMessage(message) {
        // Check if error toast already exists
        let errorToast = document.getElementById('live-update-error-toast');
        
        if (!errorToast) {
            errorToast = document.createElement('div');
            errorToast.id = 'live-update-error-toast';
            errorToast.className = 'toast toast-top toast-end';
            document.body.appendChild(errorToast);
        }
        
        errorToast.innerHTML = `
            <div class="alert alert-error">
                <i class="fas fa-exclamation-circle"></i>
                <span>${message}</span>
            </div>
        `;
        
        // Auto-hide after 5 seconds
        setTimeout(() => {
            errorToast.remove();
        }, 5000);
    }
    
    /**
     * Get current state (useful for debugging)
     */
    getState() {
        return {
            ...this.state,
            gamesData: Array.from(this.state.gamesData.values())
        };
    }
    
    /**
     * Clean up
     */
    destroy() {
        this.stop();
        document.removeEventListener('visibilitychange', this.handleVisibilityChange);
        console.log('[LiveScoreUpdater] Destroyed');
    }
}

// Export for use in other scripts
if (typeof module !== 'undefined' && module.exports) {
    module.exports = LiveScoreUpdater;
}
