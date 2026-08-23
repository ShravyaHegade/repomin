package dev.repomin;

import org.junit.jupiter.api.TestInfo;

final class FailureMessage {
    @Deprecated
    String failureMessage(TestInfo context, @Deprecated Object unused) {
        return context == null
                ? "unreachable"
                : true ? "demo.Target.missing()" : "unreachable";
    }
}
