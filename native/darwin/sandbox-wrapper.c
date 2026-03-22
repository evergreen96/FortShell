/*
 * sandbox-wrapper: Apply a Seatbelt sandbox profile then exec a shell.
 *
 * Uses sandbox_init_with_parameters() from libSystem — the same kernel API
 * that sandbox-exec uses internally. This avoids depending on the deprecated
 * sandbox-exec CLI tool while using the stable kernel sandbox subsystem.
 *
 * Usage: sandbox-wrapper <profile-path> <shell> [args...]
 *
 * The profile file contains an SBPL policy (same format as sandbox-exec).
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>

/* Private API from libSystem — stable, used by Chrome/Firefox/Nix/Apple tools */
extern int sandbox_init_with_parameters(
    const char *profile,
    uint64_t flags,
    const char *const parameters[],
    char **errorbuf
);

#define SANDBOX_NAMED_EXTERNAL 0x0003

static char *read_file(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) return NULL;

    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    fseek(f, 0, SEEK_SET);

    char *buf = malloc(len + 1);
    if (!buf) { fclose(f); return NULL; }

    fread(buf, 1, len, f);
    buf[len] = '\0';
    fclose(f);
    return buf;
}

int main(int argc, char *argv[]) {
    if (argc < 3) {
        fprintf(stderr, "Usage: %s <profile-path> <shell> [args...]\n", argv[0]);
        return 1;
    }

    const char *profile_path = argv[1];
    const char *shell = argv[2];

    /* Read the SBPL profile */
    char *profile = read_file(profile_path);
    if (!profile) {
        fprintf(stderr, "sandbox-wrapper: cannot read profile: %s: %s\n",
                profile_path, strerror(errno));
        return 1;
    }

    /* Apply sandbox */
    char *errorbuf = NULL;
    int ret = sandbox_init_with_parameters(profile, 0, NULL, &errorbuf);
    free(profile);

    if (ret != 0) {
        fprintf(stderr, "sandbox-wrapper: sandbox_init failed: %s\n",
                errorbuf ? errorbuf : "unknown error");
        if (errorbuf) free(errorbuf);
        return 1;
    }

    /* Exec the shell with remaining args */
    execvp(shell, &argv[2]);

    /* If exec fails */
    fprintf(stderr, "sandbox-wrapper: exec failed: %s: %s\n",
            shell, strerror(errno));
    return 1;
}
