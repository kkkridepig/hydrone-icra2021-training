source /opt/ros/noetic/setup.bash
source ~/hydrone_ws/devel/setup.bash
roslaunch gazebo_ros empty_world.launch \
  world_name:=$(rospack find hydrone_aerial_underwater_gazebo)/worlds/aerial_underwater.world \
  paused:=true \
  use_sim_time:=true \
  gui:=true
