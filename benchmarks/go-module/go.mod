module example.com/repomin-go-fixture

go 1.22

require (
    example.com/required v1.0.0
    example.com/unused v1.0.0
)

replace (
    example.com/required v1.0.0 => ./required
    example.com/unused v1.0.0 => ./unused
)

exclude example.com/old v0.1.0
retract [v0.0.1, v0.0.2]
