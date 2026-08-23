plugins {
    base
    java
}

configurations {
    create("unusedClasspath")
}

dependencies {
    add("unusedClasspath", "org.example:unused:1.0")
}

tasks.register("reproduceFailure") {
    doLast {
        if (providers.gradleProperty("required.flag").orNull != "true") {
            throw GradleException("DIFFERENT_FAILURE: required property is missing")
        }
        throw NoSuchMethodError("demo.Target.missing()")
    }
}
