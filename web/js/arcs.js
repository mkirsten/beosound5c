/**
 * Arc geometry utilities for BeoSound 5c
 *
 * Derived from Beolyd5 by Lars Baunwall
 * https://github.com/larsbaunwall/Beolyd5
 * Licensed under Apache License 2.0
 *
 * Functions adapted: polarToCartesian, drawArc, translateToRange, getArcPoint
 * Constants adapted: cx (1147), cy (387)
 */

const arcs = {
    // Use centralized constants with fallbacks
    get cx() { return window.Constants?.arc?.centerX || 1147; },
    get cy() { return window.Constants?.arc?.centerY || 387; },

    polarToCartesian(cx, cy, radius, angleInDegrees) {
        const angleInRadians = (angleInDegrees * Math.PI) / 180.0;
        return {
            x: cx + radius * Math.cos(angleInRadians),
            y: cy + radius * Math.sin(angleInRadians),
        };
    },

    drawArc(x, y, radius, startAngle, endAngle) {
        const start = this.polarToCartesian(x, y, radius, endAngle);
        const end = this.polarToCartesian(x, y, radius, startAngle);
        const largeArcFlag = endAngle - startAngle <= 180 ? '0' : '1';
        return [
            'M', start.x, start.y,
            'A', radius, radius, 0, largeArcFlag, 0, end.x, end.y
        ].join(' ');
    },

    getArcPoint(radius, radiusPadding, angle) {
        return this.polarToCartesian(this.cx, this.cy, radius + radiusPadding, angle);
    },

    translateToRange(input, fromMin, fromMax, toMin, toMax) {
        return ((input - fromMin) * (toMax - toMin) / (fromMax - fromMin)) + toMin;
    }
}; 