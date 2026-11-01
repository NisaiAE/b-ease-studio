# B-Ease Studio
# Copyright 2025 Nisai
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.


DEFAULT_BEZIER_POINTS = [0.25, 0.75, 0.75, 0.25]
DEFAULT_SPLITTER_RATIO = [0.8, 0.2]
DEFAULT_WINDOW_SIZE = [350, 400]
DEFAULT_SNAP_ENABLED = False
DEFAULT_LIST_ITEM_SIZE = 50
DEFAULT_IS_GRID_MODE = False


CURVE_COORD_MIN = -1_000_000.0
CURVE_COORD_MAX = 1_000_000.0
SPLITTER_RATIO_MIN = 0.05
SPLITTER_RATIO_MAX = 0.95
WINDOW_WIDTH_MIN = 128.0
WINDOW_HEIGHT_MIN = 300.0
WINDOW_SIZE_MAX = 8192.0


GRID_PADDING = 20
MIN_SLIDER_VALUE = 20
MAX_SLIDER_VALUE = 100
BUTTON_HEIGHT = 22
APPLY_BUTTON_HEIGHT = 30


PREVIEW_SIZE = 200
PREVIEW_PADDING = 0.1
THUMBNAIL_CACHE_LIMIT = 512


GRID_MAIN_DIVISIONS = 5
GRID_SUB_DIVISIONS = 10
SNAP_GRID_DIVISIONS = 100


MESSAGE_DISPLAY_DURATION = 2000
FADE_UPDATE_INTERVAL = 10
FADE_SPEED = 0.05


DEFAULT_COLORS = {

    "bg_color": "rgb(48, 48, 48)",
    "text_color": "rgb(230, 230, 230)",
    "btn_bg": "rgb(84, 84, 84)",
    "btn_bg_sel": "rgb(71, 114, 179)",
    "btn_outline": "rgb(61, 61, 61)",
    "btn_text": "rgb(230, 230, 230)",
    "btn_text_sel": "rgb(255, 255, 255)",
    "btn_radius": 4.000000059604645,
    "input_bg": "rgb(29, 29, 29)",
    "input_bg_focus": "rgb(24, 24, 24)",
    "input_outline": "rgb(61, 61, 61)",
    "input_text": "rgb(230, 230, 230)",
    "input_text_focus": "rgb(255, 255, 255)",
    "input_text_sel": "rgba(255, 255, 255, 0.2)",
    "input_radius": 4.000000059604645,
    "list_bg": "rgba(29, 29, 29, 0.5019607843137255)",
    "list_outline": "rgb(61, 61, 61)",
    "list_radius": 4.000000059604645,
    "list_item_bg": "rgba(255, 255, 255, 0.0)",
    "list_item_bg_sel": "rgb(71, 114, 179)",
    "list_item_radius": 4.000000059604645,
    "list_item_text": "rgb(204, 204, 204)",
    "list_item_text_sel": "rgb(255, 255, 255)",
    "scroll_handle": "rgb(84, 84, 84)",
    "splitter_color": "rgb(22, 22, 22)",
    "slider_groove": "rgb(84, 84, 84)",
    "slider_handle": "rgb(71, 114, 179)",
}


DEFAULT_QT_COLORS = {
    "graph_editor_bgcolor": (48, 48, 48),
    "graph_editor_gridcolor": (26, 26, 26),
    "graph_color": (255, 51, 82),
    "graph_handle_align_color": (240, 144, 160),
    "graph_handle_vertex_color": (255, 133, 0),
}


DEFAULT_ICON_COLOR = "rgb(230, 230, 230)"


DATA_DIRECTORY_NAME = "B-Ease_Studio_data"
SETTINGS_FILE_NAME = "settings.json"
SETTINGS2_FILE_NAME = "settings2.json"
SETTINGS2_SCHEMA_VERSION = 2
