plugins {
    alias(libs.plugins.android.library)
    alias(libs.plugins.kotlin.android)
}

android {
    namespace = "com.acme.network"
}

dependencies {
    implementation(libs.bundles.network)
}
