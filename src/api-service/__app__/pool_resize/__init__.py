import datetime
import logging
import math

import azure.functions as func

from ..onefuzzlib.pools import Node, Pool, Scaleset
from ..onefuzzlib.tasks.main import Task
from onefuzztypes.enums import NodeState, PoolState, ScalesetState


def scale_up(pool, scalesets, nodes_needed):
    logging.info(f"Nodes needed: {nodes_needed}")

    for scaleset in scalesets:
        if scaleset.state in ScalesetState.available():

            if scaleset.size < pool.max_size and scaleset.size < scaleset.max_size():

                max_size = min(scaleset.max_size, pool.max_size)
                current_size = scaleset.size
                if nodes_needed <= max_size:
                    scaleset.new_size = current_size + nodes_needed
                    return
                else:
                    scaleset.new_size = max_size
                    nodes_needed = nodes_needed - (max_size - current_size)
                scaleset.resize()

            else:
                continue

    if nodes_needed > 0:
        for _ in range(
            math.ceil(
                nodes_needed
                / min(Scaleset.scaleset_max_size(pool.image), pool.max_size)
            )
        ):
            logging.info(f"Creating Scaleset for Pool {pool.name}")
            max_nodes_scaleset = min(
                Scaleset.scaleset_max_size(pool.image), pool.max_size, nodes_needed
            )
            scaleset = Scaleset.create(
                pool_name=pool.name,
                vm_sku=pool.vm_sku,
                image=pool.image,
                region=pool.region,
                size=max_nodes_scaleset,
                spot_instances=pool.spot_instances,
                tags={"pool": pool.name},
            )
            scaleset.save()
            # don't return auths during create, only 'get' with include_auth
            scaleset.auth = None
            nodes_needed -= max_nodes_scaleset


def scale_down(scalesets):
    for scaleset in scalesets:
        nodes = Node.search_states(
            scaleset_id=scaleset.scaleset_id, states=[NodeState.free]
        )
        if not nodes:
            scaleset.new_size = scaleset.size - len(nodes)
            if scaleset.new_size <= 0:
                scaleset.shutdown()
                continue

            scaleset.resize()


def get_vm_count(tasks):
    count = 0
    for task in tasks:
        count += task.config.pool.count
    return count


def main(mytimer: func.TimerRequest) -> None:

    pools = Pool.search_states(states=[PoolState.init, PoolState.running])
    for pool in pools:
        tasks = Task.get_tasks_by_pool_name(pool.name)
        num_of_tasks = 0
        # get all the tasks (count not stopped) for the pool
        if not tasks:
            continue

        num_of_tasks = get_vm_count(tasks)
        # do scaleset logic match with pool
        # get all the scalesets for the pool
        scalesets = Scaleset.search_by_pool(pool.name)
        pool_resize = False
        for scaleset in scalesets:
            if scaleset.state == ScalesetState.resize:
                pool_resize = True
                break
            num_of_tasks = num_of_tasks - scaleset.size

        if pool_resize:
            continue

        if num_of_tasks > 0:
            # resizing scaleset or creating new scaleset.
            scale_up(pool, scalesets, num_of_tasks)
        elif num_of_tasks < 0:
            scale_down(scalesets)