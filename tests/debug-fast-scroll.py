#!/usr/bin/env python3
"""
Fast Scroll Debug Test for BeoSound 5c

This script specifically tests the fast scrolling issue when laser pointer
moves quickly to the "Now Playing" section (positions 76-123).
"""

import asyncio
import websockets
import json
import time
from datetime import datetime

class FastScrollDebugger:
    def __init__(self):
        self.websocket = None
        self.test_results = []
        
    async def run_debug_tests(self):
        print("🐛 BeoSound 5c Fast Scroll Debug Test")
        print("=" * 50)
        print("Testing rapid movements to 'Now Playing' section...")
        print()
        
        try:
            await self.connect_websocket()
            await self.test_fast_movements()
            await self.test_boundary_conditions()
            await self.test_rapid_sequences()
            self.analyze_results()
            
        except Exception as e:
            print(f"❌ Debug test failed: {str(e)}")
            print("\nTroubleshooting:")
            print("1. Ensure input.py service is running: sudo systemctl status beo-input")
            print("2. Check WebSocket port 8765 is accessible")
            print("3. Verify web server is running on port 8000")

    async def connect_websocket(self):
        print("🔌 Connecting to input WebSocket...")
        try:
            self.websocket = await websockets.connect("ws://localhost:8765/ws")
            print("✅ Connected to hardware WebSocket server")
        except Exception as e:
            raise Exception(f"WebSocket connection failed: {str(e)}")

    async def test_fast_movements(self):
        print("\n🚀 Testing Fast Movements to Now Playing Section")
        print("-" * 50)
        
        # Test rapid jumps from different starting positions to Now Playing area
        test_scenarios = [
            {"from": 30, "to": 120, "description": "Settings → Now Playing (fast)"},
            {"from": 60, "to": 115, "description": "Music → Now Playing (fast)"},
            {"from": 10, "to": 100, "description": "Showing → Now Playing (very fast)"},
            {"from": 45, "to": 123, "description": "Scenes → Now Playing (max fast)"},
        ]
        
        for scenario in test_scenarios:
            await self.test_rapid_movement(
                scenario["from"], 
                scenario["to"], 
                scenario["description"]
            )
            await asyncio.sleep(0.5)  # Brief pause between tests

    async def test_rapid_movement(self, start_pos, end_pos, description):
        print(f"Testing: {description}")
        
        # Send starting position
        await self.send_laser_position(start_pos)
        await asyncio.sleep(0.1)  # Brief settle time
        
        # Record start time
        start_time = time.time()
        
        # Send ending position immediately (simulate fast movement)
        await self.send_laser_position(end_pos)
        
        # Wait for UI to settle
        await asyncio.sleep(0.3)
        
        # Calculate timing
        response_time = (time.time() - start_time) * 1000
        
        # Determine expected view
        expected_view = self.get_expected_view(end_pos)
        
        # Record result
        result = {
            "test": description,
            "start_position": start_pos,
            "end_position": end_pos,
            "expected_view": expected_view,
            "response_time_ms": round(response_time, 1),
            "timestamp": datetime.now().isoformat()
        }
        
        self.test_results.append(result)
        
        print(f"  Position {start_pos} → {end_pos}: Expected '{expected_view}' ({result['response_time_ms']}ms)")

    async def test_boundary_conditions(self):
        print("\n🎯 Testing Boundary Conditions")
        print("-" * 50)
        
        # Test positions right at the boundaries of Now Playing section
        boundary_tests = [
            {"pos": 75, "desc": "Just before Now Playing (should be Music)"},
            {"pos": 76, "desc": "Start of Now Playing section"}, 
            {"pos": 120, "desc": "Near end of Now Playing"},
            {"pos": 123, "desc": "Maximum position (should be Now Playing)"},
        ]
        
        for test in boundary_tests:
            await self.test_boundary_position(test["pos"], test["desc"])

    async def test_boundary_position(self, position, description):
        print(f"Boundary test: Position {position} - {description}")
        
        # Start from opposite end to create movement
        start_pos = 10 if position > 60 else 120
        
        await self.send_laser_position(start_pos)
        await asyncio.sleep(0.1)
        
        start_time = time.time()
        await self.send_laser_position(position)
        await asyncio.sleep(0.2)
        
        response_time = (time.time() - start_time) * 1000
        expected_view = self.get_expected_view(position)
        
        result = {
            "test": f"Boundary: {description}",
            "position": position,
            "expected_view": expected_view,
            "response_time_ms": round(response_time, 1),
            "is_boundary": True
        }
        
        self.test_results.append(result)
        print(f"  Position {position}: Expected '{expected_view}' ({result['response_time_ms']}ms)")

    async def test_rapid_sequences(self):
        print("\n⚡ Testing Rapid Sequence Movements")
        print("-" * 50)
        
        # Test rapid sequence of movements (simulates very fast laser movement)
        positions = [30, 50, 70, 90, 110, 123]
        
        print("Sending rapid sequence: 30 → 50 → 70 → 90 → 110 → 123")
        
        start_time = time.time()
        
        for i, pos in enumerate(positions):
            await self.send_laser_position(pos)
            await asyncio.sleep(0.05)  # Very fast movement
            print(f"  Step {i+1}: Position {pos}")
        
        # Wait for final settle
        await asyncio.sleep(0.5)
        
        total_time = (time.time() - start_time) * 1000
        final_expected = self.get_expected_view(positions[-1])
        
        result = {
            "test": "Rapid Sequence",
            "sequence": positions,
            "final_position": positions[-1],
            "expected_final_view": final_expected,
            "total_time_ms": round(total_time, 1),
            "is_sequence": True
        }
        
        self.test_results.append(result)
        print(f"  Final position {positions[-1]}: Expected '{final_expected}' (Total: {result['total_time_ms']}ms)")

    async def send_laser_position(self, position):
        """Send laser position event via WebSocket"""
        if self.websocket:
            event = {
                "type": "laser",
                "data": {"position": position}
            }
            await self.websocket.send(json.dumps(event))

    def get_expected_view(self, position):
        """Determine expected view based on position"""
        if 3 <= position <= 25:
            return 'menu/showing'
        elif 26 <= position <= 35:
            return 'menu/settings'
        elif 36 <= position <= 42:
            return 'menu/security'
        elif 43 <= position <= 52:
            return 'menu/scenes'
        elif 53 <= position <= 75:
            return 'menu/music'
        elif 76 <= position <= 123:
            return 'menu/playing'
        else:
            return 'unknown'

    def analyze_results(self):
        print("\n📊 Fast Scroll Debug Analysis")
        print("=" * 50)
        
        # Analyze timing patterns
        fast_movements = [r for r in self.test_results if not r.get('is_boundary') and not r.get('is_sequence')]
        boundary_tests = [r for r in self.test_results if r.get('is_boundary')]
        sequence_tests = [r for r in self.test_results if r.get('is_sequence')]
        
        print(f"Fast Movements Tested: {len(fast_movements)}")
        print(f"Boundary Conditions: {len(boundary_tests)}")
        print(f"Sequence Tests: {len(sequence_tests)}")
        print()
        
        # Timing analysis
        if fast_movements:
            response_times = [r['response_time_ms'] for r in fast_movements]
            avg_time = sum(response_times) / len(response_times)
            max_time = max(response_times)
            min_time = min(response_times)
            
            print("⏱️  Timing Analysis:")
            print(f"  Average response time: {avg_time:.1f}ms")
            print(f"  Fastest response: {min_time:.1f}ms")
            print(f"  Slowest response: {max_time:.1f}ms")
            
            if max_time > 500:
                print("  ⚠️  Warning: Slow responses detected (>500ms)")
            if min_time < 100:
                print("  ⚠️  Warning: Very fast responses (<100ms) - may indicate UI not updating")
        
        print()
        
        # Now Playing section analysis
        now_playing_tests = [r for r in self.test_results if 'end_position' in r and r['end_position'] >= 76]
        
        if now_playing_tests:
            print("🎵 Now Playing Section Analysis:")
            for test in now_playing_tests:
                pos = test.get('end_position', test.get('position', 'N/A'))
                time_ms = test.get('response_time_ms', test.get('total_time_ms', 0))
                print(f"  Position {pos}: {time_ms}ms")
        
        print()
        
        # Recommendations
        print("🔧 Recommendations:")
        
        if any(r.get('response_time_ms', 0) > 500 for r in self.test_results):
            print("  • UI response is slow - check for performance bottlenecks")
            print("  • Consider reducing transition animations")
        
        if any(r.get('response_time_ms', 0) < 100 for r in self.test_results):
            print("  • Very fast responses may indicate laser events are processed")
            print("    but UI is not updating correctly")
            print("  • Check cursor-handler.js processLaserEvent function")
        
        boundary_issues = [r for r in boundary_tests if r['position'] in [75, 76]]
        if boundary_issues:
            print("  • Boundary position issues detected")
            print("  • Check position-to-angle mapping in cursor-handler.js")
            print("  • Verify MIN_LASER_POS, MID_LASER_POS, MAX_LASER_POS calibration")
        
        print("\n💡 Next Steps:")
        print("1. Check browser console for '[DEBUG] Fast scroll detected' messages")
        print("2. Verify menuAnimationState transitions in ui.js")
        print("3. Test with real hardware to confirm position calibration")
        print("4. Use browser dev tools to inspect DOM changes during fast movements")

async def main():
    debugger = FastScrollDebugger()
    await debugger.run_debug_tests()

if __name__ == "__main__":
    asyncio.run(main())