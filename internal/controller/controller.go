package controller

import (
	"context"
	"fmt"
	"log/slog"
	"strings"
	"time"

	"github.com/magicorn/epictetus/internal/cloudflare"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/util/runtime"
	"k8s.io/apimachinery/pkg/util/wait"
	"k8s.io/client-go/informers"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/tools/cache"
	"k8s.io/client-go/util/workqueue"
)

// Controller wires together the node and service informers, a rate-limiting work
// queue, and the Reconciler. Node events are deduplicated by the queue —
// bursts of MODIFIED events for the same node collapse into a single reconciliation.
type Controller struct {
	nodeInformer cache.SharedIndexInformer
	svcInformer  cache.SharedIndexInformer
	factory      informers.SharedInformerFactory
	queue        workqueue.RateLimitingInterface
	reconciler   *Reconciler
	store        *ServiceStore
	cfClient     *cloudflare.Client
	syncPeriod   time.Duration
}

func New(
	client kubernetes.Interface,
	reconciler *Reconciler,
	store *ServiceStore,
	cfClient *cloudflare.Client,
	syncPeriod time.Duration,
) *Controller {
	factory := informers.NewSharedInformerFactory(client, 0)

	ctrl := &Controller{
		nodeInformer: factory.Core().V1().Nodes().Informer(),
		svcInformer:  factory.Core().V1().Services().Informer(),
		factory:      factory,
		queue:        workqueue.NewRateLimitingQueue(workqueue.DefaultControllerRateLimiter()),
		reconciler:   reconciler,
		store:        store,
		cfClient:     cfClient,
		syncPeriod:   syncPeriod,
	}

	ctrl.nodeInformer.AddEventHandler(cache.ResourceEventHandlerFuncs{
		AddFunc:    ctrl.onNodeAdd,
		UpdateFunc: ctrl.onNodeUpdate,
		DeleteFunc: ctrl.onNodeDelete,
	})

	ctrl.svcInformer.AddEventHandler(cache.ResourceEventHandlerFuncs{
		AddFunc: func(obj interface{}) {
			if svc, ok := obj.(*corev1.Service); ok {
				store.Update(svc)
			}
		},
		UpdateFunc: func(_, newObj interface{}) {
			if svc, ok := newObj.(*corev1.Service); ok {
				store.Update(svc)
			}
		},
		DeleteFunc: func(obj interface{}) {
			svc, ok := obj.(*corev1.Service)
			if !ok {
				if tombstone, ok := obj.(cache.DeletedFinalStateUnknown); ok {
					svc, ok = tombstone.Obj.(*corev1.Service)
					if !ok {
						return
					}
				} else {
					return
				}
			}
			store.Delete(svc.Namespace + "/" + svc.Name)
		},
	})

	return ctrl
}

func (c *Controller) onNodeAdd(obj interface{}) {
	node, ok := obj.(*corev1.Node)
	if !ok {
		return
	}
	slog.Debug("node added to cluster", "node", node.Name)
	c.enqueue(node.Name)
}

func (c *Controller) onNodeUpdate(old, new interface{}) {
	oldNode, ok1 := old.(*corev1.Node)
	newNode, ok2 := new.(*corev1.Node)
	if !ok1 || !ok2 {
		return
	}
	if nodeStateChanged(oldNode, newNode) {
		slog.Debug("node state changed", "node", newNode.Name,
			"ready", readyCondition(newNode.Status.Conditions),
			"taints", taintKeys(newNode.Spec.Taints))
		c.enqueue(newNode.Name)
	}
}

func (c *Controller) onNodeDelete(obj interface{}) {
	node, ok := obj.(*corev1.Node)
	if !ok {
		if tombstone, ok := obj.(cache.DeletedFinalStateUnknown); ok {
			node, ok = tombstone.Obj.(*corev1.Node)
			if !ok {
				return
			}
		} else {
			return
		}
	}
	slog.Info("node removed from cluster", "node", node.Name)
	// "deleted:" prefix signals the worker to run ReconcileDeleted instead.
	c.queue.Add("deleted:" + node.Name)
}

func (c *Controller) enqueue(nodeName string) {
	c.queue.Add(nodeName)
}

// Run starts the informers, waits for cache sync, then launches workers and the
// periodic full-sync ticker. Blocks until ctx is cancelled.
func (c *Controller) Run(ctx context.Context, workers int) error {
	defer runtime.HandleCrash()
	defer c.queue.ShutDown()

	slog.Info("starting epictetus controller", "workers", workers, "sync_period", c.syncPeriod)

	c.factory.Start(ctx.Done())

	slog.Info("waiting for informer cache sync")
	if !cache.WaitForCacheSync(ctx.Done(), c.nodeInformer.HasSynced, c.svcInformer.HasSynced) {
		return fmt.Errorf("timed out waiting for informer caches to sync")
	}
	slog.Info("informer caches synced — watching for events")

	// Run one full sync immediately to establish a consistent baseline from the
	// current cluster state. This is cheaper than reconciling every node
	// individually from the ADDED events that were queued during cache fill.
	c.fullSync(ctx)
	c.drainQueue()

	for i := 0; i < workers; i++ {
		go wait.UntilWithContext(ctx, c.runWorker, time.Second)
	}

	go c.runPeriodicSync(ctx)

	<-ctx.Done()
	slog.Info("controller shutting down")
	return nil
}

// drainQueue discards all items currently sitting in the queue.
// Called after the initial fullSync so we don't re-reconcile every node individually.
func (c *Controller) drainQueue() {
	drained := 0
	for c.queue.Len() > 0 {
		item, shutdown := c.queue.Get()
		if shutdown {
			return
		}
		c.queue.Done(item)
		c.queue.Forget(item)
		drained++
	}
	if drained > 0 {
		slog.Debug("drained startup queue after initial sync", "items", drained)
	}
}

func (c *Controller) runWorker(ctx context.Context) {
	for c.processNext(ctx) {
	}
}

func (c *Controller) processNext(ctx context.Context) bool {
	key, quit := c.queue.Get()
	if quit {
		return false
	}
	defer c.queue.Done(key)

	keyStr := key.(string)
	var err error

	if nodeName, isDeleted := strings.CutPrefix(keyStr, "deleted:"); isDeleted {
		err = c.reconciler.ReconcileDeleted(ctx, nodeName)
	} else {
		item, exists, getErr := c.nodeInformer.GetIndexer().GetByKey(keyStr)
		if getErr != nil {
			slog.Error("informer cache lookup error", "key", keyStr, "err", getErr)
			c.queue.AddRateLimited(key)
			return true
		}
		if !exists {
			// Deleted between enqueue and processing
			err = c.reconciler.ReconcileDeleted(ctx, keyStr)
		} else {
			err = c.reconciler.Reconcile(ctx, item.(*corev1.Node))
		}
	}

	if err != nil {
		slog.Error("reconcile failed, requeuing", "key", keyStr, "err", err)
		c.queue.AddRateLimited(key)
	} else {
		c.queue.Forget(key)
	}
	return true
}

func (c *Controller) runPeriodicSync(ctx context.Context) {
	ticker := time.NewTicker(c.syncPeriod)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if err := c.cfClient.RefreshZones(ctx); err != nil {
				slog.Warn("zone refresh failed", "err", err)
			}
			c.fullSync(ctx)
		}
	}
}

func (c *Controller) fullSync(ctx context.Context) {
	start := time.Now()

	objs := c.nodeInformer.GetIndexer().List()
	nodes := make([]*corev1.Node, 0, len(objs))
	for _, obj := range objs {
		if node, ok := obj.(*corev1.Node); ok {
			nodes = append(nodes, node)
		}
	}

	if err := c.reconciler.PerformFullSync(ctx, nodes); err != nil {
		slog.Error("full sync failed", "duration", time.Since(start), "err", err)
	} else {
		slog.Info("full sync complete", "duration", time.Since(start), "nodes", len(nodes))
	}
}

// nodeStateChanged returns true only for changes that affect DNS — taints,
// ready condition, or external IP. Filters out the high-frequency MODIFIED
// events Kubernetes emits for heartbeats and unrelated field updates.
func nodeStateChanged(old, new *corev1.Node) bool {
	return extractExternalIP(old) != extractExternalIP(new) ||
		taintsChanged(old.Spec.Taints, new.Spec.Taints) ||
		readyConditionChanged(old.Status.Conditions, new.Status.Conditions)
}

func taintsChanged(a, b []corev1.Taint) bool {
	if len(a) != len(b) {
		return true
	}
	set := make(map[string]struct{}, len(a))
	for _, t := range a {
		set[t.Key+"/"+string(t.Effect)] = struct{}{}
	}
	for _, t := range b {
		if _, ok := set[t.Key+"/"+string(t.Effect)]; !ok {
			return true
		}
	}
	return false
}

func readyConditionChanged(a, b []corev1.NodeCondition) bool {
	return conditionStatus(a, corev1.NodeReady) != conditionStatus(b, corev1.NodeReady)
}

func conditionStatus(conditions []corev1.NodeCondition, t corev1.NodeConditionType) corev1.ConditionStatus {
	for _, c := range conditions {
		if c.Type == t {
			return c.Status
		}
	}
	return corev1.ConditionUnknown
}
