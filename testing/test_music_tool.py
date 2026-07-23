import os
import shutil
import sys
import tempfile
from pathlib import Path

# Add parent directory to path so imports work when run directly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tools.music_tool import MusicTools

def test_music_tool():
    print("Testing MusicTools initialization...")
    temp_dir = tempfile.mkdtemp()
    try:
        ambient_dir = os.path.join(temp_dir, "ambient")
        combat_dir = os.path.join(temp_dir, "combat")
        os.makedirs(ambient_dir, exist_ok=True)
        os.makedirs(combat_dir, exist_ok=True)

        with open(os.path.join(ambient_dir, "description.txt"), "w") as f:
            f.write("Calm, relaxing ambient music.")
        with open(os.path.join(ambient_dir, "track1.mp3"), "w") as f:
            f.write("mock audio")
        with open(os.path.join(ambient_dir, "track2.mp3"), "w") as f:
            f.write("mock audio")

        with open(os.path.join(combat_dir, "track1.mp3"), "w") as f:
            f.write("mock audio")

        config = {
            "music": {
                "playlists_folder": temp_dir
            }
        }
        music_tools = MusicTools(config)
        
        # 1. Test listing playlists
        print("\n1. Testing list_playlists()...")
        res = music_tools.list_playlists()
        print("Result:")
        print(res)
        
        assert "ambient" in res, "Ambient playlist missing in listing"
        assert "combat" in res, "Combat playlist missing in listing"
        assert "track1.mp3" in res or "track2.mp3" in res, "MP3 track listing missing"
        assert "Calm, relaxing" in res, "Description text missing or incorrect"
        
        # 2. Test playing a playlist
        print("\n2. Testing play_playlist()...")
        called_playlist = None
        called_tracks = []
        
        def mock_on_play(playlist_name, tracks):
            nonlocal called_playlist, called_tracks
            called_playlist = playlist_name
            called_tracks = tracks
            print(f"Callback triggered: playlist='{playlist_name}' tracks={tracks}")
            
        music_tools.on_play_playlist = mock_on_play
        
        play_res = music_tools.play_playlist("ambient")
        print("play_playlist() returned:", play_res)
        
        assert called_playlist == "ambient", "Callback playlist name mismatch"
        assert len(called_tracks) == 2, "Expected 2 tracks in ambient playlist"
        assert called_tracks[0] == "/playlists/ambient/track1.mp3", "Unexpected track path"
        assert called_tracks[1] == "/playlists/ambient/track2.mp3", "Unexpected track path"
        assert "Successfully started playing" in play_res, "Play status response mismatch"
        
        # 3. Test playing non-existent playlist
        print("\n3. Testing non-existent playlist...")
        fail_res = music_tools.play_playlist("non_existent")
        print("play_playlist('non_existent') returned:", fail_res)
        assert "Error: Playlist" in fail_res, "Expected error message for missing playlist"
        
        # 4. Test pause/resume callbacks
        print("\n4. Testing pause_playlist() and resume_playlist()...")
        paused_triggered = False
        resumed_triggered = False

        def mock_on_pause():
            nonlocal paused_triggered
            paused_triggered = True
            print("Mock Pause Callback triggered.")

        def mock_on_resume():
            nonlocal resumed_triggered
            resumed_triggered = True
            print("Mock Resume Callback triggered.")

        music_tools.on_pause_playlist = mock_on_pause
        music_tools.on_resume_playlist = mock_on_resume

        pause_res = music_tools.pause_playlist()
        resume_res = music_tools.resume_playlist()

        assert paused_triggered, "Pause callback was not triggered"
        assert resumed_triggered, "Resume callback was not triggered"
        assert "Successfully paused" in pause_res, "Pause status message mismatch"
        assert "Successfully resumed" in resume_res, "Resume status message mismatch"
        
        print("\nALL MUSIC TOOL TESTS PASSED!")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

if __name__ == "__main__":
    test_music_tool()
