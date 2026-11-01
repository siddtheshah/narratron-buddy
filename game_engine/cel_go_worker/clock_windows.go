//go:build windows

package main

import (
	"syscall"
	"unsafe"
)

// Go's Windows time.Now uses interrupt time, which can be too coarse for
// individual helper conversions. Use the same high-resolution monotonic clock
// as Python's perf_counter for benchmark instrumentation.
const metricsClock = "QueryPerformanceCounter"

var kernel = syscall.NewLazyDLL("kernel32.dll")
var queryCounter = kernel.NewProc("QueryPerformanceCounter")
var queryFrequency = kernel.NewProc("QueryPerformanceFrequency")
var counterFrequency = func() int64 {
	var frequency int64
	ok, _, err := queryFrequency.Call(uintptr(unsafe.Pointer(&frequency)))
	if ok == 0 {
		panic(err)
	}
	return frequency
}()

func clockNS() int64 {
	var counter int64
	ok, _, err := queryCounter.Call(uintptr(unsafe.Pointer(&counter)))
	if ok == 0 {
		panic(err)
	}
	// Split before multiplying to avoid overflow at long system uptimes.
	return counter/counterFrequency*1_000_000_000 + counter%counterFrequency*1_000_000_000/counterFrequency
}
