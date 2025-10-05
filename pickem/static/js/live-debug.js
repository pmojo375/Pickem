/**
 * Quick diagnostic tool for live updates
 * Add this to the page temporarily to debug issues
 */

(function() {
    console.log('=== Live Update Diagnostics ===');
    
    // 1. Check if updater exists
    if (typeof window.liveScoreUpdater !== 'undefined') {
        console.log('✓ LiveScoreUpdater exists');
        
        const state = window.liveScoreUpdater.getState();
        console.log('State:', {
            isPolling: state.isPolling,
            isPaused: state.isPaused,
            hasLiveGames: state.hasLiveGames,
            currentInterval: state.currentInterval,
            gamesCount: state.gamesData.length,
            retryCount: state.retryCount
        });
    } else {
        console.error('✗ LiveScoreUpdater not found!');
    }
    
    // 2. Check for games container
    const container = document.getElementById('games-container');
    if (container) {
        console.log('✓ Games container found');
    } else {
        console.error('✗ Games container NOT found!');
        console.log('This means you probably have no picks, so live updates wont initialize');
    }
    
    // 3. Check for game elements
    const gameElements = document.querySelectorAll('[data-game-id]');
    console.log(`Found ${gameElements.length} game elements with data-game-id`);
    
    if (gameElements.length > 0) {
        // Check first game element
        const firstGame = gameElements[0];
        console.log('First game element:', {
            gameId: firstGame.getAttribute('data-game-id'),
            externalId: firstGame.getAttribute('data-external-id'),
            hasHomeScore: firstGame.querySelector('[data-score="home"]') !== null,
            hasAwayScore: firstGame.querySelector('[data-score="away"]') !== null,
            hasStatus: firstGame.querySelector('[data-game-status]') !== null
        });
    }
    
    // 4. Test API manually
    console.log('Testing API endpoint...');
    fetch('/api/games/?limit=100')
        .then(r => r.json())
        .then(data => {
            console.log('API Response:', {
                gamesCount: data.games.length,
                metadata: data.metadata,
                firstGame: data.games[0]
            });
        })
        .catch(err => {
            console.error('API Error:', err);
        });
    
    // 5. Check for JavaScript errors
    console.log('Check above for any errors (red text)');
    
    console.log('=== End Diagnostics ===');
})();

