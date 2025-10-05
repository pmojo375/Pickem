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
        
        console.log(`[LiveScoreUpdater] Next poll in ${this.state.currentInterval}ms`);
        
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
            // Query for recent games using local browser date
            // Format date as YYYY-MM-DD in local timezone (not UTC)
            const now = new Date();
            const year = now.getFullYear();
            const month = String(now.getMonth() + 1).padStart(2, '0');
            const day = String(now.getDate()).padStart(2, '0');
            const localDate = `${year}-${month}-${day}`;
            
            // Also get yesterday's date to catch games that started late
            const yesterday = new Date(now);
            yesterday.setDate(yesterday.getDate() - 1);
            const yesterdayDate = `${yesterday.getFullYear()}-${String(yesterday.getMonth() + 1).padStart(2, '0')}-${String(yesterday.getDate()).padStart(2, '0')}`;
            
            // Query without date filter to get all recent games
            // This avoids timezone issues and catches games from the last 2-3 days
            const url = `${this.config.apiEndpoint}?limit=100`;
            
            console.log(`[LiveScoreUpdater] Polling ${url} (Local date: ${localDate}, UTC would be: ${new Date().toISOString().split('T')[0]})`);
            
            const response = await fetch(url, {
                method: 'GET',
                headers: {
                    'Accept': 'application/json',
                },
                signal: AbortSignal.timeout(10000) // 10 second timeout
            });
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const data = await response.json();
            
            // Process the response
            this.processGamesData(data);
            
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
        
        console.log(`[LiveScoreUpdater] Processing ${games.length} games`);
        
        games.forEach(game => {
            // Check if game is live
            const isLive = this.isGameLive(game);
            if (isLive) {
                liveGameCount++;
            }
            
            // Check if this is a new game or if it has changed
            const existingGame = this.state.gamesData.get(game.id);
            
            if (!existingGame) {
                // New game
                this.state.gamesData.set(game.id, game);
            } else {
                // Check what changed
                const changes = this.detectChanges(existingGame, game);
                
                if (changes.length > 0) {
                    console.log(`[LiveScoreUpdater] Game ${game.id} changed:`, changes);
                    
                    // Update stored game data
                    this.state.gamesData.set(game.id, game);
                    
                    // Update UI
                    this.updateGameUI(game, changes);
                    
                    updatedGames.push({ game, changes });
                }
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
        
        // Check clock changes
        if (oldGame.clock !== newGame.clock) {
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
        
        return changes;
    }
    
    /**
     * Update game UI with new data
     */
    updateGameUI(game, changes) {
        // Find game element by data-game-id attribute
        const gameElement = document.querySelector(`[data-game-id="${game.id}"]`);
        
        if (!gameElement) {
            console.warn(`[LiveScoreUpdater] Game element not found for game ${game.id}`);
            return;
        }
        
        // Save scroll position
        const scrollY = window.scrollY;
        
        changes.forEach(change => {
            switch (change.type) {
                case 'home_score':
                    this.updateScore(gameElement, 'home', game.home_score);
                    break;
                    
                case 'away_score':
                    this.updateScore(gameElement, 'away', game.away_score);
                    break;
                    
                case 'quarter':
                case 'clock':
                    this.updateGameStatus(gameElement, game);
                    break;
                    
                case 'is_final':
                    this.updateGameStatus(gameElement, game);
                    break;
            }
        });
        
        // Restore scroll position
        window.scrollTo(0, scrollY);
    }
    
    /**
     * Update score display with animation
     */
    updateScore(gameElement, team, newScore) {
        const scoreSelector = `[data-score="${team}"]`;
        const scoreElement = gameElement.querySelector(scoreSelector);
        
        if (!scoreElement) {
            console.warn(`[LiveScoreUpdater] Score element not found for ${team}`);
            return;
        }
        
        // Update score value
        scoreElement.textContent = newScore || '-';
        
        // Add pulse animation
        scoreElement.classList.remove('score-pulse');
        // Force reflow to restart animation
        void scoreElement.offsetWidth;
        scoreElement.classList.add('score-pulse');
        
        // Remove animation class after duration
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
        const existingTimeElement = statusElement.querySelector('.text-xs');
        if (existingTimeElement) {
            kickoffTimeText = existingTimeElement.textContent;
        }
        
        let statusHTML = '';
        
        if (game.is_final) {
            statusHTML = `
                <div class="text-center">
                    <div class="text-2xl md:text-3xl font-bold text-success mb-1">FINAL</div>
                    ${kickoffTimeText ? `<div class="text-xs text-base-content/60">${kickoffTimeText}</div>` : ''}
                </div>
            `;
        } else if (game.quarter) {
            statusHTML = `
                <div class="text-center">
                    <div class="text-xl md:text-2xl font-bold text-warning animate-pulse mb-1">
                        Q${game.quarter}
                    </div>
                    <div class="text-sm md:text-base font-semibold text-warning">${game.clock || ''}</div>
                    ${kickoffTimeText ? `<div class="text-xs text-base-content/60 mt-1">${kickoffTimeText}</div>` : ''}
                </div>
            `;
        } else {
            statusHTML = `
                <div class="text-center">
                    ${kickoffTimeText ? `<div class="text-base md:text-lg font-semibold text-base-content/70 mb-1">${kickoffTimeText}</div>` : ''}
                    <div class="text-xs text-base-content/50">Scheduled</div>
                </div>
            `;
        }
        
        statusElement.innerHTML = statusHTML;
    }
    
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
        
        console.log(`[LiveScoreUpdater] Retrying in ${Math.round(retryDelay)}ms (attempt ${this.state.retryCount}/${this.config.maxRetries})`);
        
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

