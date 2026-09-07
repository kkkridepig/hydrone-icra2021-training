source /opt/ros/noetic/setup.bash
source ~/hydrone_ws/devel/setup.bash
python3 \
~/hydrone_ws/src/hydrone_deep_rl_icra/hydrone_aerial_underwater_gazebo/scripts/hydrone_transition_demo_fixed.py \
_end_x:=5.0 \
_cruise_speed:=0.5 \
_acceleration:=0.2 \
_start_hold:=3.0 \
_underwater_hold:=6.0 \
_final_hold:=3.0
