"""Native BREP witnesses for the three fixed default constructor controls.

These test nominal geometry features, not purchased-hardware compliance.
"""
import math
import statistics


def bounds(raw):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(raw, box, False, False)
    a, b = box.CornerMin(), box.CornerMax()
    return (a.X(), a.Y(), a.Z()), (b.X(), b.Y(), b.Z())


def helical_edges(raw):
    from build123d import Shape
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    pitches, errors = [], []
    for edge in Shape.cast(raw).edges():
        curve = BRepAdaptor_Curve(edge.wrapped)
        first, last = curve.FirstParameter(), curve.LastParameter()
        if not math.isfinite(first) or not math.isfinite(last):
            raise ValueError('Unbounded edge cannot establish thread geometry')
        if last == first:
            continue
        points = [curve.Value(first + (last-first)*n/64) for n in range(65)]
        radii = [math.hypot(p.X(), p.Y()) for p in points]
        if max(radii)-min(radii) > .01 or abs(statistics.mean(radii)-3) > .01:
            continue
        angles = [math.atan2(p.Y(), p.X()) for p in points]
        unwrapped = [0.]
        for a, b in zip(angles, angles[1:]):
            unwrapped.append(unwrapped[-1] + (b-a+math.pi)%(2*math.pi)-math.pi)
        span, dz = unwrapped[-1], points[-1].Z()-points[0].Z()
        if abs(span) < math.pi/2 or abs(dz) < .2:
            continue
        pitch = dz*2*math.pi/span
        error = max(abs(p.Z()-points[0].Z()-angle*pitch/(2*math.pi))
                    for p, angle in zip(points, unwrapped))
        pitches.append(pitch)
        errors.append(error)
    return {'edge_count':len(pitches), 'signed_pitches_mm':sorted(pitches),
            'z_angle_max_error_mm':max(errors) if errors else None,
            'passed':len(pitches)>=2 and all(abs(p-1)<=.01 for p in pitches) and max(errors)<=.003}


def cosmetic_thread_envelope(raw, diameter, minimum_axial_extent):
    """Check the B-rep's smooth major-diameter cylinder; metadata supplies the thread claim."""
    from build123d import Shape
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    candidates = []
    for solid in Shape.cast(raw).solids():
        for face in solid.faces():
            surface = BRepAdaptor_Surface(face.wrapped, True)
            if surface.GetType() != GeomAbs_Cylinder:
                continue
            cylinder = surface.Cylinder()
            direction = cylinder.Axis().Direction()
            if abs(cylinder.Radius() - diameter / 2) > .01 or abs(direction.Z()) < .999:
                continue
            low, high = bounds(face.wrapped)
            extent = high[2] - low[2]
            if extent + .01 >= minimum_axial_extent:
                candidates.append({'radius_mm': cylinder.Radius(), 'axial_extent_mm': extent})
    return {'cylindrical_faces': candidates,
            'passed': bool(candidates) and max(item['axial_extent_mm'] for item in candidates) + .01 >= minimum_axial_extent}


def axial_tooth_profile(raw):
    from build123d import Shape
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN, TopAbs_ON, TopAbs_OUT
    from OCP.gp import gp_Pnt
    solids = [(bounds(s.wrapped), BRepClass3d_SolidClassifier(s.wrapped)) for s in Shape.cast(raw).solids()]
    def occupied(radius, z):
        for (low,high), classifier in solids:
            if not (low[0]-1e-7<=radius<=high[0]+1e-7 and low[1]-1e-7<=0<=high[1]+1e-7 and low[2]-1e-7<=z<=high[2]+1e-7):
                continue
            classifier.Perform(gp_Pnt(radius,0,z),1e-7)
            state=classifier.State()
            if state in (TopAbs_IN,TopAbs_ON):
                return True
            if state != TopAbs_OUT:
                raise ValueError('Unknown material state in thread profile')
        return False
    def transition(radius,a,b,state_a):
        for _ in range(9):
            mid=(a+b)/2
            if occupied(radius,mid)==state_a:a=mid
            else:b=mid
        return (a+b)/2
    slices=[]
    for radius in (2.65,2.9):
        lower,step,intervals=2.,.025,[]
        previous=occupied(radius,lower)
        start=lower if previous else None
        for n in range(1,161):
            z=lower+n*step
            current=occupied(radius,z)
            if current and not previous:
                start=transition(radius,z-step,z,False)
            elif previous and not current:
                end=transition(radius,z-step,z,True)
                if start is not None and start>lower+1e-6:
                    intervals.append([start,end])
                start=None
            previous=current
        widths=[b-a for a,b in intervals]
        centers=[(a+b)/2 for a,b in intervals]
        pitches=[b-a for a,b in zip(centers,centers[1:])]
        # Nominal external M6x1 crest width 1/8 pitch, 60-degree flanks.
        expected_width=.125 + 2*(3-radius)/math.tan(math.pi/3)
        slices.append({'radius_mm':radius,'angle_deg':0,'intervals_z_mm':intervals,
                       'expected_width_mm':expected_width,
                       'mean_width_mm':statistics.mean(widths) if widths else None,
                       'center_pitches_mm':pitches,
                       'passed':len(intervals)>=3 and all(abs(w-expected_width)<=.01 for w in widths)
                                and all(abs(p-1)<=.01 for p in pitches)})
    return {'sample_step_mm':.025,'slices':slices,'passed':all(s['passed'] for s in slices)}


def bearing_internals(raw):
    from build123d import Shape
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Sphere
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN
    from OCP.gp import gp_Pnt
    solids=Shape.cast(raw).solids()
    balls,races=[],[]
    for solid in solids:
        faces=solid.faces()
        surface=BRepAdaptor_Surface(faces[0].wrapped,True) if len(faces)==1 else None
        if surface is None or surface.GetType()!=GeomAbs_Sphere:
            low,high=bounds(solid.wrapped)
            dims=[b-a for a,b in zip(low,high)]
            kind='unclassified'
            points=[]
            if all(abs(a-b)<=.01 for a,b in zip(dims,(12.1,12.1,7))):
                kind='inner';points=[(3.99,False),(4.01,True),(6.06,False)]
            elif all(abs(a-b)<=.01 for a,b in zip(dims,(22,22,7))):
                kind='outer';points=[(8.79,False),(8.81,True),(11.01,False)]
            classifier=BRepClass3d_SolidClassifier(solid.wrapped)
            probes=[]
            for radius,expected in points:
                classifier.Perform(gp_Pnt(radius,0,3.5),1e-7)
                probes.append({'point_mm':[radius,0,3.5],'expected_solid':expected,
                               'actual_solid':classifier.State()==TopAbs_IN})
            races.append({'kind':kind,'bbox_mm':dims,'material_probes':probes,
                          'passed':bool(points) and all(p['actual_solid']==p['expected_solid'] for p in probes)})
            continue
        sphere=surface.Sphere();p=sphere.Location()
        balls.append({'center_mm':[p.X(),p.Y(),p.Z()],'radius_mm':sphere.Radius()})
    balls.sort(key=lambda ball:math.atan2(ball['center_mm'][1],ball['center_mm'][0])%(2*math.pi))
    angles=[math.atan2(b['center_mm'][1],b['center_mm'][0])%(2*math.pi) for b in balls]
    gaps=[(b-a)%(2*math.pi)*180/math.pi for a,b in zip(angles,angles[1:]+angles[:1])] if angles else []
    matching=all(abs(b['radius_mm']-1.71875)<=.01
                 and abs(math.hypot(*b['center_mm'][:2])-7.425)<=.01
                 and abs(b['center_mm'][2]-3.5)<=.01 for b in balls)
    races.sort(key=lambda race:race['kind'])
    return {'solid_count':len(solids),'balls':balls,'races':races,'angular_gaps_deg':gaps,
            'passed':len(solids)==14 and len(balls)==12 and matching and all(abs(g-30)<=.01 for g in gaps)
                     and [r['kind'] for r in races]==['inner','outer'] and all(r['passed'] for r in races)}


def geometry_witness(raw, part_id):
    if part_id.startswith('bearing.'):
        return bearing_internals(raw)
    if part_id.startswith('thread.'):
        thread = cosmetic_thread_envelope(raw, 6.0, 8.0)
        return {'thread_representation': 'cosmetic', 'cosmetic_thread': thread, 'passed': thread['passed']}
    if part_id.startswith('fastener.'):
        thread = cosmetic_thread_envelope(raw, 6.0, 1.0)
        return {'thread_representation': 'cosmetic', 'cosmetic_thread': thread, 'passed': thread['passed']}
    edges=helical_edges(raw)
    return {'helical_edges':edges,'passed':edges['passed']}
