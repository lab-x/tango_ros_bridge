#!/usr/bin/env python
"""
A simple echo server
"""

from udp import UDPhandle
import rospy
from sensor_msgs.msg import CompressedImage, PointCloud
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped, PoseArray, Pose, Point, Quaternion
from std_msgs.msg import Float64, Float64MultiArray, String, Int32, Header
import sys
from tf.transformations import euler_from_quaternion, quaternion_from_euler, quaternion_inverse
from math import pi
import numpy as np
import tf
from tf.transformations import euler_from_quaternion, rotation_matrix, quaternion_from_matrix, quaternion_matrix


class TransformHelpers:
    """ Some convenience functions for translating between various representions of a pose. """

    @staticmethod
    def convert_translation_rotation_to_pose(translation, rotation):
        """ Convert from representation of a pose as translation and rotation (Quaternion) tuples to a geometry_msgs/Pose message """
        return Pose(position=Point(x=translation[0],y=translation[1],z=translation[2]), orientation=Quaternion(x=rotation[0],y=rotation[1],z=rotation[2],w=rotation[3]))

    @staticmethod
    def convert_pose_inverse_transform(pose):
        """ Helper method to invert a transform (this is built into the tf C++ classes, but ommitted from Python) """
        translation = np.zeros((4,1))
        translation[0] = -pose.position.x
        translation[1] = -pose.position.y
        translation[2] = -pose.position.z
        translation[3] = 1.0

        rotation = (pose.orientation.x, pose.orientation.y, pose.orientation.z, pose.orientation.w)
        rotation_inverse = quaternion_inverse(rotation)
        rot_mat = quaternion_matrix(rotation_inverse)
        transformed_translation = rot_mat.dot(translation)

        translation = (transformed_translation[0], transformed_translation[1], transformed_translation[2])
        rotation = rotation_inverse
        return (translation, rotation)


def fix_area_learning_to_odom_transform(area_learning_pose, odom_pose, tf_broadcaster , tf_listener):
    global transform_translation, transform_rotation
    """ Update area_learning to odom transform. """
    try:
        tf_listener.waitForTransform("device","odom",odom_pose.header.stamp,rospy.Duration(1.0))
    except:
        return

    if (abs((odom_pose.header.stamp - area_learning_pose.header.stamp).to_sec()) > 0.5 or
        (area_learning_pose.pose.position.x == 0 and area_learning_pose.pose.position.y == 0 and area_learning_pose.pose.position.z == 0) or
        (odom_pose.pose.position.x == 0 and odom_pose.pose.position.y == 0 and odom_pose.pose.position.z == 0)):
        if transform_translation != None and transform_rotation != None:
            tf_broadcaster.sendTransform(transform_translation, transform_rotation, odom_pose.header.stamp, "odom", "area_learning")
        return

    (translation, rotation) = TransformHelpers.convert_pose_inverse_transform(area_learning_pose.pose)
    p = PoseStamped(pose=TransformHelpers.convert_translation_rotation_to_pose(translation,rotation),
                    header=Header(stamp=odom_pose.header.stamp,frame_id="device"))
    odom_to_area_learning = tf_listener.transformPose("odom", p)
    (transform_translation, transform_rotation) = TransformHelpers.convert_pose_inverse_transform(odom_to_area_learning.pose)
    tf_broadcaster.sendTransform(transform_translation, transform_rotation, odom_pose.header.stamp, "odom", "area_learning")

""" Keeps track of whether we have a valid clock offset between
    ROS time and Tango time.  We don't care too much about
    how close these two are synched, we mostly just care that
    all nodes use the same offset """
tango_clock_valid = False
tango_clock_offset = -1.0

latest_area_learning_pose = None
latest_odom_pose = None
tf_broadcaster = None
tf_listener = None

#latest_odom_pose = None

def handle_tango_clock(msg):
    global tango_clock_offset
    tango_clock_offset = msg.data

def handle_odom_pose(msg):
    #global latest_odom_pose
    global latest_odom_pose
    latest_odom_pose = msg

rospy.init_node("pose_area_server")

port = rospy.get_param('~port_number')
pose_topic = rospy.get_param('~pose_topic')
coordinate_frame = rospy.get_param('~coordinate_frame')

transform_translation = (0.0 ,0.0, 0.0)
transform_rotation = (0.0 , 0.0 , 0.0, 1.0)

begin_pose_marker = 'POSESTARTINGRIGHTNOW\n' 
end_pose_marker = 'POSEENDINGRIGHTNOW\n'

pub_pose = rospy.Publisher(pose_topic, PoseStamped, queue_size=10)
clock_sub = rospy.Subscriber('/tango_clock', Float64, handle_tango_clock)
pose_sub = rospy.Subscriber('/tango_pose', PoseStamped, handle_odom_pose)

tf_broadcaster = tf.TransformBroadcaster()
tf_listener = tf.TransformListener()

tango_clock_valid = False
tango_clock_offset = -1.0

@UDPhandle(port=port, start_delim=begin_pose_marker, end_delim=end_pose_marker)
def handle_pkt(pkt=None):
    global tango_clock_valid 
    global tango_clock_offset
    global tf_broadcaster
    global tf_listener

    pose_vals = pkt.split(",")
    tango_timestamp = pose_vals[-3]
    pose_vals = pose_vals[0:-3]

    msg = PoseStamped()
    # might need to revisit time stamps
    msg.header.stamp = rospy.Time(tango_clock_offset + float(tango_timestamp))

    msg.header.frame_id = coordinate_frame
    print tango_timestamp
    msg.pose.position.x = float(pose_vals[0])
    msg.pose.position.y = float(pose_vals[1])
    msg.pose.position.z = float(pose_vals[2])

    # two of the rotation axes seem to be off...
    # we are fixing this in a hacky way right now
    euler_angles = euler_from_quaternion(pose_vals[3:])
    pose_vals[3:] = quaternion_from_euler(euler_angles[1],
                                          euler_angles[0]+pi/2, # this is right
                                          euler_angles[2]-pi/2)
    euler_angles_transformed = euler_from_quaternion(pose_vals[3:])

    msg.pose.orientation.x = float(pose_vals[3])
    msg.pose.orientation.y = float(pose_vals[4])
    msg.pose.orientation.z = float(pose_vals[5])
    msg.pose.orientation.w = float(pose_vals[6])

    euler_angles_depth_camera = (euler_angles_transformed[0],
                                 euler_angles_transformed[1],
                                 euler_angles_transformed[2])
    pub_pose.publish(msg)
    latest_area_learning_pose = msg
    if latest_area_learning_pose and latest_odom_pose:
        fix_area_learning_to_odom_transform(latest_area_learning_pose, latest_odom_pose, tf_broadcaster, tf_listener)

handle_pkt()
