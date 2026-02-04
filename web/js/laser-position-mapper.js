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
            MENU_ITEMS: c.menuItems,
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
            { title: 'SHOWING', path: 'menu/showing' },
            { title: 'SYSTEM', path: 'menu/system' },
            { title: 'SECURITY', path: 'menu/security' },
            { title: 'SCENES', path: 'menu/scenes' },
            { title: 'MUSIC', path: 'menu/music' },
            { title: 'PLAYING', path: 'menu/playing' }
        ],
        MENU_ANGLE_STEP: 5
    };
})();

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
    const { MENU_ANGLE_STEP } = LASER_MAPPING_CONFIG;
    return getMenuStartAngle() + index * MENU_ANGLE_STEP;
}

/**
 * Find the closest menu item to an angle
 * @param {number} angle - Angle to find closest menu item for
 * @param {boolean} requireExactMatch - If true, only return if within 2 degrees
 * @returns {object|null} Menu item object or null if none close enough
 */
function findClosestMenuItem(angle, requireExactMatch = false) {
    const { MENU_ITEMS } = LASER_MAPPING_CONFIG;
    let closestItem = null;
    let closestDistance = Infinity;
    
    for (let i = 0; i < MENU_ITEMS.length; i++) {
        const itemAngle = getMenuItemAngle(i);
        const distance = Math.abs(angle - itemAngle);
        
        if (distance < closestDistance) {
            closestDistance = distance;
            closestItem = {
                ...MENU_ITEMS[i],
                index: i,
                angle: itemAngle,
                distance: distance
            };
        }
    }
    
    // If exact match required, only return if within 2 degrees
    if (requireExactMatch && closestDistance > 2) {
        return null;
    }
    
    return closestItem;
}

/**
 * Main function: Convert laser position to UI view
 * @param {number} position - Laser position (3-123)
 * @returns {object} View information with path, reason, and metadata
 */
function getViewForLaserPosition(position) {
    const { TOP_OVERLAY_START, BOTTOM_OVERLAY_START } = LASER_MAPPING_CONFIG;
    
    // Convert position to angle
    const angle = laserPositionToAngle(position);
    
    // Check for overlay zones first
    if (angle >= BOTTOM_OVERLAY_START) {
        return {
            path: 'menu/playing',
            reason: 'bottom_overlay',
            angle: angle,
            position: position,
            isOverlay: true
        };
    }
    
    if (angle <= TOP_OVERLAY_START) {
        return {
            path: 'menu/showing',
            reason: 'top_overlay',
            angle: angle,
            position: position,
            isOverlay: true
        };
    }
    
    // In menu area - find closest menu item
    const exactMenuItem = findClosestMenuItem(angle, true); // Within 2 degrees
    const closestMenuItem = findClosestMenuItem(angle, false); // Always find closest
    
    if (exactMenuItem) {
        // Close enough to be considered "selected"
        return {
            path: exactMenuItem.path,
            reason: 'menu_item_selected',
            angle: angle,
            position: position,
            isOverlay: false,
            menuItem: exactMenuItem
        };
    } else if (closestMenuItem) {
        // Show closest menu item but not "selected"
        return {
            path: closestMenuItem.path,
            reason: 'menu_item_closest',
            angle: angle,
            position: position,
            isOverlay: false,
            menuItem: closestMenuItem
        };
    }
    
    // Fallback (should never happen)
    return {
        path: 'menu/playing',
        reason: 'fallback',
        angle: angle,
        position: position,
        isOverlay: false
    };
}

/**
 * Get detailed mapping information for debugging
 * @param {number} position - Laser position (3-123)
 * @returns {object} Detailed mapping information
 */
function getDetailedMappingInfo(position) {
    const { TOP_OVERLAY_START, BOTTOM_OVERLAY_START, MENU_ITEMS } = LASER_MAPPING_CONFIG;
    const angle = laserPositionToAngle(position);
    const view = getViewForLaserPosition(position);
    
    // Calculate all menu item angles for reference
    const menuItemAngles = MENU_ITEMS.map((item, index) => ({
        ...item,
        index: index,
        angle: getMenuItemAngle(index)
    }));
    
    return {
        input: {
            position: position,
            angle: angle
        },
        output: view,
        thresholds: {
            topOverlayStart: TOP_OVERLAY_START,
            bottomOverlayStart: BOTTOM_OVERLAY_START,
            menuStartAngle: getMenuStartAngle()
        },
        menuItems: menuItemAngles,
        debug: {
            isInTopOverlay: angle <= TOP_OVERLAY_START,
            isInBottomOverlay: angle >= BOTTOM_OVERLAY_START,
            isInMenuArea: angle > TOP_OVERLAY_START && angle < BOTTOM_OVERLAY_START
        }
    };
}

// Export functions for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    // Node.js environment
    module.exports = {
        getViewForLaserPosition,
        getDetailedMappingInfo,
        laserPositionToAngle,
        findClosestMenuItem,
        getMenuItemAngle,
        getMenuStartAngle,
        LASER_MAPPING_CONFIG
    };
} else {
    // Browser environment
    window.LaserPositionMapper = {
        getViewForLaserPosition,
        getDetailedMappingInfo,
        laserPositionToAngle,
        findClosestMenuItem,
        getMenuItemAngle,
        getMenuStartAngle,
        LASER_MAPPING_CONFIG
    };
}