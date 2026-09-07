#!/usr/bin/env python3
"""Read-only sensor, publisher, and Stage 2 obstacle checks during a gate."""
import json
from pathlib import Path
import sys
import time
import rospy
from gazebo_msgs.srv import GetModelProperties, GetModelState
from sensor_msgs.msg import LaserScan
from nav_msgs.msg import Odometry
from trajectory_msgs.msg import MultiDOFJointTrajectory

stage = int(sys.argv[1])
rospy.init_node('gpu_gate_graph_check', anonymous=True, disable_signals=True)
topics = {}
def receive(message, topic):
    row = topics.setdefault(topic, {'count': 0})
    row['count'] += 1
    row['frame_id'] = message.header.frame_id
    if isinstance(message, LaserScan): row['beams'] = len(message.ranges)
    if isinstance(message, Odometry): row['child_frame_id'] = message.child_frame_id
subscribers = [rospy.Subscriber('/hydrone_aerial_underwater0/' + name, cls, receive, name)
               for name, cls in [('scan', LaserScan), ('ground_truth/odometry', Odometry),
                                 ('odometry_sensor1/odometry', Odometry),
                                 ('command/trajectory', MultiDOFJointTrajectory)]]
time.sleep(3)
assert len(topics) == 4, topics
assert topics['scan']['beams'] == 20
state = rospy.get_master().getSystemState()
assert state[0] == 1
publishers = dict(state[2][0]).get('/hydrone_aerial_underwater0/cmd_vel', [])
assert publishers == ['/icra2021_agent'], publishers
report = {'stage': stage, 'topics': topics, 'cmd_vel_publishers': publishers, 'obstacles': []}
if stage == 2:
    properties = rospy.ServiceProxy('/gazebo/get_model_properties', GetModelProperties)
    pose = rospy.ServiceProxy('/gazebo/get_model_state', GetModelState)
    for number, xy in enumerate([(2, 2), (-2, -2), (2, -2), (-2, 2)], 1):
        name = 'obstacle_' + str(number)
        p = properties(name)
        s = pose(name, 'world')
        assert p.success and p.is_static and s.success, name
        xyz = [s.pose.position.x, s.pose.position.y, s.pose.position.z]
        assert abs(xyz[0] - xy[0]) < 1e-6 and abs(xyz[1] - xy[1]) < 1e-6
        report['obstacles'].append({'name': name, 'static': p.is_static, 'position': xyz})
report['passed'] = True
path = Path(__file__).resolve().parents[2] / ('logs/server/graph-stage%d.json' % stage)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
