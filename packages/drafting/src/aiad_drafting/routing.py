"""Route hole leaders around other holes using a small visibility graph."""
from __future__ import annotations
import heapq
import math


def segment_distance(center, start, end):
    dx,dy=end[0]-start[0],end[1]-start[1]
    length=dx*dx+dy*dy
    t=max(0,min(1,((center[0]-start[0])*dx+(center[1]-start[1])*dy)/length)) if length else 0
    return math.dist(center,(start[0]+t*dx,start[1]+t*dy))


def hole_leader_path(circles, selected_indices, end, blocked=()):
    """Return (hole index, points) or None, keeping all other bores clear.

    circles contains (index, center, radius) in sheet mm. The stock's outer
    outline may be crossed to reach an external callout. Other view rectangles
    and all other holes are obstacles. The first point lies on the chosen bore.
    """
    from .layout import _segment_intersects_rect
    clearance=0.6
    nodes=[]
    sources={}
    for index,center,radius in circles:
        ring=(radius+clearance+0.25)/math.cos(math.pi/16)
        for step in range(16):
            angle=2*math.pi*step/16
            ux,uy=math.cos(angle),math.sin(angle)
            point=(center[0]+ring*ux,center[1]+ring*uy)
            if any(other!=index and math.dist(point,c)<r+clearance for other,c,r in circles):
                continue
            if any(rect.xmin<=point[0]<=rect.xmax and rect.ymin<=point[1]<=rect.ymax for rect in blocked):
                continue
            node=len(nodes)
            nodes.append(point)
            if index in selected_indices:
                tip=(center[0]+radius*ux,center[1]+radius*uy)
                if all(other==index or segment_distance(c,tip,point)>=r+clearance for other,c,r in circles):
                    sources[node]=(index,tip)
    target=len(nodes)
    nodes.append(end)
    distance={node:math.dist(tip,nodes[node]) for node,(_,tip) in sources.items()}
    previous={node:None for node in sources}
    origin={node:node for node in sources}
    queue=[(cost,node) for node,cost in distance.items()]
    heapq.heapify(queue)
    visited=set()
    while queue:
        cost,node=heapq.heappop(queue)
        if node in visited:
            continue
        visited.add(node)
        if node==target:
            path=[]
            cursor=node
            while cursor is not None:
                path.append(nodes[cursor]); cursor=previous[cursor]
            source=origin[node]
            index,tip=sources[source]
            return index,[tip,*reversed(path)]
        first=nodes[node]
        for other,last in enumerate(nodes):
            if other in visited or other==node:
                continue
            weight=math.dist(first,last)+0.5
            if cost+weight>=distance.get(other,float('inf')):
                continue
            if any(segment_distance(c,first,last)<r+clearance-1e-8 for _,c,r in circles):
                continue
            if any(_segment_intersects_rect(first,last,rect) for rect in blocked):
                continue
            distance[other]=cost+weight
            previous[other]=node
            origin[other]=origin[node]
            heapq.heappush(queue,(cost+weight,other))
    return None
