import concurrent.futures
import time
import queue


def run_parallel_tasks(tasks, args, code_function, log):
    q = queue.Queue()
    start = time.time()

    def feed_the_workers():
        new_tasks = False
        for task in tasks:
            if "status" not in task:
                task["status"] = "waiting"
            if len(task["dependencies"]) == 0 and task["status"] == "waiting":
                task['status'] = "running"
                new_tasks = True
                q.put(task)
        if not new_tasks:
            waiting = [t["key"] for t in tasks if t["status"] == "waiting"]
            succeeded = [t["key"] for t in tasks if t["status"] == "succeeded"]
            failed = [t["key"] for t in tasks if t["status"] == "failed"]
            log.info("_______________________", time=False)
            log.info("Run complete in {}s".format(round(time.time() - start, 1)), time=False)
            log.info("{} tasks succeeded".format(len(succeeded)), time=False)
            log.info("{} tasks failed: {}".format(len(failed), failed), time=False)
            log.info("{} tasks had dependency failures: {}".format(len(waiting), waiting), time=False)
            log.info("_______________________", time=False)

    def run(task):
        log.info("{} starting".format(task["key"]))
        start_time = time.time()
        try:
            code_function(task, args)
        except Exception as e:
            log.info("{} failed in {}s. See task log for details.".format(task["key"], round(time.time() - start_time, 1)))
            for t in tasks:
                if task["key"] == t["key"]:
                    t["status"] = "failed"
        else:
            log.info("{} completed in {}s".format(task["key"], round(time.time() - start_time, 1)))
            for t in tasks:
                if task["key"] in t["dependencies"]:
                    t["dependencies"].remove(task["key"])
                if task["key"] == t["key"]:
                    t["status"] = "succeeded"
        feed_the_workers()

    with concurrent.futures.ThreadPoolExecutor(max_workers=args["max_workers"]) as executor:
        future_to_task = {executor.submit(feed_the_workers): 'load'}
        while future_to_task:
            done, not_done = concurrent.futures.wait(future_to_task, return_when=concurrent.futures.FIRST_COMPLETED)
            while not q.empty():
                task = q.get()
                future_to_task[executor.submit(run, task)] = task
            for future in done:
                future.result()
                del future_to_task[future]
