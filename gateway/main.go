package main

import (
	"fmt"

	_ "github.com/golang-jwt/jwt/v5"
	_ "github.com/google/uuid"
	_ "github.com/redis/go-redis/v9"
)

func main() {
	fmt.Println("Belgrade OS Edge Gateway")
}
