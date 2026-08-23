package dev.repomin;

import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInfo;

final class TriggerTest {
    private static final int UNUSED_FIELD = 42;

    private void unusedMethod() {
        System.out.println("unrelated");
    }

    @Test
    void preservesTheOriginalFailure(TestInfo context) {
        int noise = 1;
        if (noise > 0) {
            noise++;
        }
        throw new NoSuchMethodError(
                new FailureMessage().failureMessage(context, null));
    }
}
