//go:build !windows

package main

import "time"

const metricsClock = "time.Since monotonic"

var clockEpoch = time.Now()

func clockNS() int64 { return time.Since(clockEpoch).Nanoseconds() }
