package main

import (
    "fmt"
    "example.com/required"
)

func main() {
    if required.Value() != "required" {
        fmt.Println("DIFFERENT_FAILURE")
        return
    }
    panic("ORIGINAL_FAILURE")
}
