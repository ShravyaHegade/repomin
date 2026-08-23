fn main() {
    if required_lib::value() != "required" {
        std::process::exit(2);
    }
    panic!("ORIGINAL_FAILURE");
}
