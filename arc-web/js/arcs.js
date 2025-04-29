const arcs = {
    cx: 1147, // Horizontal center
    cy: 387,  // Vertical center

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