"""Standard Gazebo wrench service; requests are not measured force readings."""
from diagnostic_core import LEGACY
import rospy
from gazebo_msgs.srv import ApplyBodyWrench, ApplyBodyWrenchRequest, BodyRequest, GetModelProperties
from ros_io import Gazebo

class DiagnosticGazebo(Gazebo):
    def __init__(self,c):
        self.body=None
        super().__init__(c)
        self.apply_wrench=self._service('/gazebo/apply_body_wrench',ApplyBodyWrench)
        self.clear_wrench=self._service('/gazebo/clear_body_wrenches',BodyRequest)
        properties=self._service('/gazebo/get_model_properties',GetModelProperties)(c['namespace'])
        if not properties.success:raise RuntimeError(properties.status_message)
        choices=[b for b in properties.body_names if b.split('::')[-1]==c['namespace']+'/base_link']
        if len(choices)!=1:raise RuntimeError('Cannot resolve base link: '+str(properties.body_names))
        self.body=choices[0] if '::' in choices[0] else c['namespace']+'::'+choices[0]
        self.clear_pulse()
        self.probe_receipt=self.pulse(dict(force_y_N=0.,torque_x_Nm=0.,force_seconds=.2),1.)
        self.clear_pulse()
    def pulse(self,profile,sign):
        req=ApplyBodyWrenchRequest()
        req.body_name=self.body
        # Avoid the service's non-world transformation path; zero reference point.
        req.reference_frame='world'
        req.wrench.force.y=sign*profile['force_y_N']
        req.wrench.torque.x=sign*profile['torque_x_Nm']
        before=rospy.Time.now().to_sec()
        req.start_time=rospy.Time.from_sec(before)
        req.duration=rospy.Duration.from_sec(profile['force_seconds'])
        result=self.apply_wrench(req)
        after=rospy.Time.now().to_sec()
        if not result.success:raise RuntimeError('Gazebo rejected wrench: '+result.status_message)
        if after<before or after-before>=profile['force_seconds']:
            raise RuntimeError('Wrench service timing cannot identify an active interval')
        return dict(body=self.body,frame='world',force_y_N=req.wrench.force.y,torque_x_Nm=req.wrench.torque.x,
                    duration=profile['force_seconds'],start_lower=before,start_upper=after,
                    end_lower=before+profile['force_seconds'],end_upper=after+profile['force_seconds'],
                    accepted=True,interpretation='Acknowledged scheduled load, not a force sensor reading')
    def clear_pulse(self):
        if self.body is not None:self.clear_wrench(self.body)
    def reset(self,spec):
        self.clear_pulse()
        return super().reset(spec)
    def close(self):
        try:self.clear_pulse()
        finally:super().close()
