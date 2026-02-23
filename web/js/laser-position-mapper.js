/**
 * Pure Laser Position Mapper for BeoSound 5c
 * 
 * Converts laser position (3-123) to the appropriate UI view.
 * This function consolidates all position-to-view mapping logic in one place
 * for easy testing, debugging, and modification.
 */

/**
 * Configuration constants for laser position mapping
 * Uses centralized Constants when available (browser), falls back to local values (Node.js testing)
 */
const LASER_MAPPING_CONFIG = (function() {
    // Check if Constants is available (browser environment)
    if (typeof window !== 'undefined' && window.Constants) {
        const c = window.Constants;
        return {
            MIN_LASER_POS: c.laser.minPosition,
            MID_LASER_POS: c.laser.midPosition,
            MAX_LASER_POS: c.laser.maxPosition,
            MIN_ANGLE: c.laser.minAngle,
            MID_ANGLE: c.laser.midAngle,
            MAX_ANGLE: c.laser.maxAngle,
            TOP_OVERLAY_START: c.overlays.topOverlayStart,
            BOTTOM_OVERLAY_START: c.overlays.bottomOverlayStart,
            // Dynamic getter: returns runtime override if set, otherwise Constants
            get MENU_ITEMS() {
                return window._dynamicMenuItems || c.menuItems;
            },
            MENU_ANGLE_STEP: c.arc.menuAngleStep
        };
    }

    // Fallback for Node.js testing environment
    return {
        MIN_LASER_POS: 3,
        MID_LASER_POS: 72,
        MAX_LASER_POS: 123,
        MIN_ANGLE: 150,
        MID_ANGLE: 180,
        MAX_ANGLE: 210,
        TOP_OVERLAY_START: 160,
        BOTTOM_OVERLAY_START: 200,
        MENU_ITEMS: [
            { title: 'PLAYING', path: 'menu/playing' },
            { title: 'SPOTIFY', path: 'menu/spotify' },
            { title: 'SCENES', path: 'menu/scenes' },
            { title: 'SYSTEM', path: 'menu/system' },
            { title: 'SHOWING', path: 'menu/showing' }
        ],
        MENU_ANGLE_STEP: 5
    };
})();

/**
 * Update the menu items used for laser position mapping at runtime.
 * @param {Array} items - Array of {title, path} menu item objects
 */
function updateMenuItems(items) {
    if (typeof window !== 'undefined') {
        window._dynamicMenuItems = items.map(i => ({ title: i.title, path: i.path }));
    }
}

/**
 * Convert laser position to angle using the current calibration
 * @param {number} position - Laser position (3-123)
 * @returns {number} Angle in degrees (150-210)
 */
function laserPositionToAngle(position) {
    const { MIN_LASER_POS, MID_LASER_POS, MAX_LASER_POS, MIN_ANGLE, MID_ANGLE, MAX_ANGLE } = LASER_MAPPING_CONFIG;
    
    // Clamp position to valid range
    const clampedPos = Math.max(MIN_LASER_POS, Math.min(MAX_LASER_POS, position));
    
    let angle;
    
    if (clampedPos <= MIN_LASER_POS) {
        // At or below minimum
        angle = MIN_ANGLE;
    } else if (clampedPos < MID_LASER_POS) {
        // Between min and mid, map to MIN_ANGLE-MID_ANGLE
        const slope = (MID_ANGLE - MIN_ANGLE) / (MID_LASER_POS - MIN_LASER_POS);
        angle = MIN_ANGLE + slope * (clampedPos - MIN_LASER_POS);
    } else if (clampedPos <= MAX_LASER_POS) {
        // Between mid and max, map to MID_ANGLE-MAX_ANGLE
        const slope = (MAX_ANGLE - MID_ANGLE) / (MAX_LASER_POS - MID_LASER_POS);
        angle = MID_ANGLE + slope * (clampedPos - MID_LASER_POS);
    } else {
        // Above maximum
        angle = MAX_ANGLE;
    }
    
    return angle;
}

/**
 * Calculate the starting angle for menu items
 * @returns {number} Starting angle for first menu item
 */
function getMenuStartAngle() {
    const { MENU_ITEMS, MENU_ANGLE_STEP } = LASER_MAPPING_CONFIG;
    const totalSpan = MENU_ANGLE_STEP * (MENU_ITEMS.length - 1);
    return 180 - totalSpan / 2;
}

/**
 * Get the angle for a specific menu item
 * @param {number} index - Menu item index (0-based)
 * @returns {number} Angle for the menu item
 */
function getMenuItemAngle(index) {
    const { MENU_ITEMS, MENU_ANGLE_STEP } = LASER_MAPPING_CONFIG;
    return getMenuStartAngle() + (MENU_ITEMS.length - 1 - index) * MENU_ANGLE_STEP;
}

/**
 * Resolve which menu item (if any) the laser is pointing at.
 * Each item owns itemAngle ± halfStep. One call, one result.
 * @param {number} position - Laser position (3-123)
 * @returns {object} { selectedIndex, path, angle, isOverlay }
 */
function resolveMenuSelection(position) {
    const { TOP_OVERLAY_START, BOTTOM_OVERLAY_START, MENU_ITEMS, MENU_ANGLE_STEP } = LASER_MAPPING_CONFIG;
    const angle = laserPositionToAngle(position);

    if (angle <= TOP_OVERLAY_START)  return { selectedIndex: -1, path: null, angle, isOverlay: true };
    if (angle >= BOTTOM_OVERLAY_START) return { selectedIndex: -1, path: null, angle, isOverlay: true };

    const halfStep = MENU_ANGLE_STEP / 2;
    for (let i = 0; i < MENU_ITEMS.length; i++) {
        if (Math.abs(angle - getMenuItemAngle(i)) <= halfStep)
            return { selectedIndex: i, path: MENU_ITEMS[i].path, angle, isOverlay: false };
    }
    return { selectedIndex: -1, path: null, angle, isOverlay: false }; // gap between items
}

/**
 * Convert angle back to laser position (inverse of laserPositionToAngle).
 * Used by mouse/emulator input paths to set laserPosition from an angle.
 * @param {number} angle - Angle in degrees (150-210)
 * @returns {number} Laser position (3-123)
 */
function angleToLaserPosition(angle) {
    const { MIN_LASER_POS, MID_LASER_POS, MAX_LASER_POS, MIN_ANGLE, MID_ANGLE, MAX_ANGLE } = LASER_MAPPING_CONFIG;

    const clampedAngle = Math.max(MIN_ANGLE, Math.min(MAX_ANGLE, angle));

    if (clampedAngle <= MID_ANGLE) {
        const slope = (MID_LASER_POS - MIN_LASER_POS) / (MID_ANGLE - MIN_ANGLE);
        return MIN_LASER_POS + slope * (clampedAngle - MIN_ANGLE);
    } else {
        const slope = (MAX_LASER_POS - MID_LASER_POS) / (MAX_ANGLE - MID_ANGLE);
        return MID_LASER_POS + slope * (clampedAngle - MID_ANGLE);
    }
}

// Export functions for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    // Node.js environment
    module.exports = {
        resolveMenuSelection,
        laserPositionToAngle,
        angleToLaserPosition,
        getMenuItemAngle,
        getMenuStartAngle,
        updateMenuItems,
        LASER_MAPPING_CONFIG
    };
} else {
    // Browser environment
    window.LaserPositionMapper = {
        resolveMenuSelection,
        laserPositionToAngle,
        angleToLaserPosition,
        getMenuItemAngle,
        getMenuStartAngle,
        updateMenuItems,
        LASER_MAPPING_CONFIG
    };
}