import numpy as np
from unittest.mock import MagicMock, patch

import camera_clip


def test_fetch_camera_frame_retries_until_frame():
    class DummyStream:
        def __init__(self):
            self.read = MagicMock(side_effect=[None, None, 'frame'])

        def start(self):
            return self

        def stop(self):
            pass

    dummy_stream = DummyStream()

    with patch('camera_clip.VideoStream', return_value=dummy_stream), \
         patch('cv2.imencode', return_value=(True, np.array([1, 2, 3], dtype=np.uint8))), \
         patch('camera_clip.time.sleep', return_value=None):
        result = camera_clip.fetch_camera_frame('ip', 'u', 'p', max_attempts=5)

    assert result == bytes([1, 2, 3])
    assert dummy_stream.read.call_count == 3


def test_fetch_camera_frame_custom_path():
    dummy_stream = MagicMock()
    dummy_stream.read.return_value = 'frame'
    dummy_stream.start.return_value = dummy_stream

    with patch('camera_clip.VideoStream', return_value=dummy_stream) as vs, \
         patch('cv2.imencode', return_value=(True, np.array([1], dtype=np.uint8))):
        camera_clip.fetch_camera_frame('ip', 'u', 'p', rtsp_path='/foo', max_attempts=1)

    vs.assert_called_with('rtsp://u:p@ip:554/foo')
