source /opt/ros/noetic/setup.bash
source ~/hydrone_ws/devel/setup.bash
roslaunch hydrone_aerial_underwater_gazebo \
  spawn_hydrone_only_fixed.launch \
  x:=0 y:=10 z:=1
